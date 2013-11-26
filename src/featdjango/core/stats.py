import time
import sqlite3
import re

from twisted.internet import reactor
from twisted.enterprise import adbapi
from zope.interface import implements

from feat.common import log, defer, error, text_helper
from feat.agencies import journaler
from featdjango.core import threadpool


class SqliteStorage(object):

    def __init__(self, filename):
        self._filename = filename
        self._db = None
        self._processing = False

        # [(created, started)]
        self._waiting_times = journaler.EntriesCache()
        # [(method, path, started, finished)]
        self._jobs_done = journaler.EntriesCache()

    ### public methods for storing data ###

    def log_waiting_time(self, created, started):
        self._waiting_times.append((created, started))
        self._next_tick()

    def log_job_done(self, method, path, started, finished):
        self._jobs_done.append((method, path, started, finished))
        self._next_tick()

    ### private ###

    def _next_tick(self):
        if self._processing:
            return
        self._processing = True
        d = defer.Deferred()
        reactor.callFromThread(d.callback, None)
        if self._db is None:
            self._db = adbapi.ConnectionPool('sqlite3', self._filename,
                                         cp_min=1, cp_max=1, cp_noisy=True,
                                         check_same_thread=False,
                                         timeout=10)

            d.addCallback(defer.drop_param, self._check_schema)
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
        d.addCallback(defer.override_result, None)
        return d

    def _create_schema(self, fail):
        fail.trap(sqlite3.OperationalError)

        commands = [
            text_helper.format_block("""
            CREATE TABLE processed_requests (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              method VARCHAR(10) NOT NULL,
              path VARCHAR(1000) NOT NULL,
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
             "ON processed_requests(method, path)"),
            ]

        def run_all(connection, commands):
            for command in commands:
                connection.execute(command)

        return self._db.runWithConnection(run_all, commands)

    def _flush_next(self):
        return self._db.runWithConnection(do_inserts,
                                          self._waiting_times, self._jobs_done)


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
        for method, path, started, finished in entries:
            connection.execute("INSERT INTO processed_requests VALUES "
                               "(null, ?, ?, DATETIME(?, 'unixepoch'), ?)",
                               (method, path, started, finished - started))
    finally:
        if entries:
            jobs_done.commit()


class Statistics(log.Logger):

    implements(threadpool.IThreadStatistics)

    job_id_pattern = re.compile("^[0-9]+-(?P<method>[^\s]+) (?P<path>.+)")

    def __init__(self, log_keeper=None, filename=":memory:"):
        log.Logger.__init__(self, log_keeper or log.get_default())

        # # (method, path) -> [(epoch_created, epoch_started, time_elapsed)]
        # self.processed = dict()
        self.storage = SqliteStorage(filename)

        # job_id -> epoch_created
        self.waiting = dict()
        # job_id -> epoch_started
        self.processing = dict()

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

        # job id is of the form "GET /path-index"
        match = self.job_id_pattern.search(job_id)
        if not match:
            self.debug("Ignoring job_id %r which doesn't match the expected"
                       " pattern.", job_id)
            return
        self.storage.log_job_done(match.group('method'),
                                  match.group('path'), started, ctime)

    def fallen_asleep(self, job_id, reason=None):
        pass

    def woken_up(self, job_id):
        pass

    def new_thread(self):
        pass

    def exit_thread(self):
        pass
