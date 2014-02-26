import time
import sqlite3
import re

from twisted.internet import reactor
from twisted.enterprise import adbapi
from zope.interface import implements

from django.core import urlresolvers

from feat.common import log, defer, error, text_helper, signal
from feat.agencies import journaler
from featdjango.core import threadpool


class SqliteStorage(object):

    def __init__(self, filename):
        self._filename = filename
        self._db = None
        self._processing = False

        # [(created, started)]
        self._waiting_times = journaler.EntriesCache()
        # [(method, viewname, started, finished)]
        self._jobs_done = journaler.EntriesCache()

        self._sighup_installed = False

    ### public methods for storing data ###

    def log_waiting_time(self, created, started):
        self._waiting_times.append((created, started))
        self._next_tick()

    def log_job_done(self, method, viewname, started, finished):
        self._jobs_done.append((method, viewname, started, finished))
        self._next_tick()

    ### public ###

    @defer.ensure_async
    def get_db(self):
        if self._db is None:
            self._db = adbapi.ConnectionPool('sqlite3', self._filename,
                                         cp_min=1, cp_max=1, cp_noisy=True,
                                         check_same_thread=False,
                                         timeout=10)
            self._install_sighup()
            return self._check_schema()
        else:
            return self._db

    def close(self):
        if self._db:
            db = self._db
            self._db = None
            db.close()
        self._uninstall_sighup()

    ### private ###

    def _next_tick(self):
        if self._processing:
            return
        self._processing = True
        d = defer.Deferred()
        reactor.callFromThread(d.callback, None)
        d.addCallback(defer.drop_param, self.get_db)
        d.addCallback(defer.drop_param, self._flush_next)
        d.addBoth(defer.bridge_param, self._finished_processing)
        d.addErrback(self._error_handler)

    def _finished_processing(self):
        self._processing = False

    def _error_handler(self, fail):
        error.handle_failure(None, fail,
                             'Failed processing with sqlite storage')

    def _check_schema(self):
        d = self._db.runQuery(
            'SELECT method FROM processed_requests LIMIT 1')
        d.addErrback(self._create_schema)
        d.addCallback(defer.override_result, self._db)
        return d

    def _create_schema(self, fail):
        fail.trap(sqlite3.OperationalError)

        commands = [
            text_helper.format_block("""
            CREATE TABLE processed_requests (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              method VARCHAR(10) NOT NULL,
              viewname VARCHAR(1000) NOT NULL,
              started DATETIME NOT NULL,
              elapsed FLOAT NOT NULL
            )
            """),
            text_helper.format_block("""
            CREATE TABLE waiting_times (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created DATETIME NOT NULL,
              elapsed FLOAT NOT NULL
            )
            """),
            ("CREATE INDEX processed_requestsx "
             "ON processed_requests(method, viewname)"),
            ]

        def run_all(connection, commands):
            for command in commands:
                connection.execute(command)

        return self._db.runWithConnection(run_all, commands)

    def _flush_next(self):
        return self._db.runWithConnection(do_inserts,
                                          self._waiting_times, self._jobs_done)

    ### private rotating database file ###

    def _sighup_handler(self, signum, frame):
        self.close()
        reactor.callWhenRunning(self.get_db)

    def _install_sighup(self):
        if self._sighup_installed:
            return
        if self._filename == ':memory:':
            return
        signal.signal(signal.SIGHUP, self._sighup_handler)
        self._sighup_installed = True

    def _uninstall_sighup(self):
        if not self._sighup_installed:
            return
        self._sighup_installed = False
        signal.unregister(signal.SIGHUP, self._sighup_handler)


def do_inserts(connection, waiting_times, jobs_done):
    try:
        entries = waiting_times.fetch()
        for created, started in entries:
            connection.execute("INSERT INTO waiting_times VALUES "
                               "(null, DATETIME(?, 'unixepoch'), ?)",
                               (created, started - created))
    finally:
        if entries:
            waiting_times.commit()

    try:
        entries = jobs_done.fetch()
        for method, viewname, started, finished in entries:
            connection.execute("INSERT INTO processed_requests VALUES "
                               "(null, ?, ?, DATETIME(?, 'unixepoch'), ?)",
                               (method, viewname, started, finished - started))
    finally:
        if entries:
            jobs_done.commit()


class Statistics(log.Logger):

    implements(threadpool.IThreadStatistics)

    job_id_pattern = re.compile("^[0-9]+-(?P<method>[^\s]+) (?P<path>.+)")

    def __init__(self, log_keeper=None, filename=":memory:"):
        log.Logger.__init__(self, log_keeper or log.get_default())

        self.storage = SqliteStorage(filename)

        # job_id -> epoch_created
        self.waiting = dict()
        # job_id -> epoch_started
        self.processing = dict()

        # number_of_threads * time_of_operation
        self._uptime_snapshot = 0
        # snapshot when the uptime was last time updated
        self._uptime_snapshot_epoch = time.time()
        self.number_of_threads = 0

        # busy time (the time the threadpool is actually doing some job)
        self.busy_time = 0

    def new_item(self, job_id, explanation=None):
        self.waiting[job_id] = time.time()

    def started_item(self, job_id):
        created = self.waiting.pop(job_id, None)
        ctime = time.time()
        if created is None:
            self.warning('job_id %r was not in self.waiting with'
                         ' started_item() was called.', job_id)
            return
        self.storage.log_waiting_time(created, ctime)

        self.processing[job_id] = ctime

    def finished_item(self, job_id):
        started = self.processing.pop(job_id, None)
        if started is None:
            self.warning('job_id %r was not in self.processing with '
                         'finished_item() was called.', job_id)
            return

        ctime = time.time()

        try:
            method, view_name = self.parse_job_id(job_id)
        except:
            pass
        else:
            self.storage.log_job_done(method, view_name, started, ctime)
            self.busy_time += ctime - started

    def fallen_asleep(self, job_id, reason=None):
        pass

    def woken_up(self, job_id):
        pass

    def new_thread(self):
        self.update_uptime()
        self.number_of_threads += 1

    def exit_thread(self):
        self.update_uptime()
        self.number_of_threads -= 1

    ### protected ###

    def parse_job_id(self, job_id):
        match = self.job_id_pattern.search(job_id)
        if not match:
            self.debug("Ignoring job_id %r which doesn't match the expected"
                       " pattern.", job_id)
            return
        try:
            resolved = urlresolvers.resolve(match.group('path'))
        except urlresolvers.Resolver404:
            # this is not call to django view, ignore
            return
        else:
            return match.group('method'), resolved.view_name

    @property
    def uptime(self):
        return self.update_uptime()

    def update_uptime(self):
        ctime = time.time()
        self._uptime_snapshot += ((ctime - self._uptime_snapshot_epoch)
                                  * self.number_of_threads)
        self._uptime_snapshot_epoch = ctime
        return self._uptime_snapshot
