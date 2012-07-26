import uuid
import time

from zope.interface import Interface, implements

from feat.common import reflect, error, formatable, log, defer

from twisted.python import threadpool, context, failure


class IThreadStatistics(Interface):

    def new_item(job_id, explanation=None):
        '''
        Called when a threadpool receives the new task.

        @param explanation: C{unicode}
        @param job_id: unique identifier of this piece of work
        '''

    def started_item(job_id):
        '''
        Called when a threadpool starts provessing the task.

        @param job_id: unique identifier of this piece of work
        '''

    def fallen_asleep(job_id, reason=None):
        '''
        Called when threadpool start sleeping waiting for a Deferred
        to finish.
        @param job_id: unique identifier of this piece of work
        @param reason: C{unicode}
        '''

    def woken_up(job_id):
        '''
        Called when threadpool is woken up.
        @param job_id: unique identifier of this piece of work
        '''

    def finished_item(job_id):
        '''
        @param job_id: unique identifier of this piece of work
        Called when the piece of work is done.
        '''

    def new_thread():
        '''
        Called when the new thread is started.
        '''

    def exit_thread():
        '''
        Called when the thread is stopped.
        '''


class StatsNap(formatable.Formatable):

    formatable.field('start_timestamp', None)
    formatable.field('finish_timestamp', None)
    formatable.field('reason', u'')

    @property
    def duration(self):
        if self.start_timestamp and self.finish_timestamp:
            return self.finish_timestamp - self.start_timestamp


class StatsRecord(formatable.Formatable):

    formatable.field('explanation', None)
    formatable.field('created_timestamp', None)
    formatable.field('start_timestamp', None)
    formatable.field('finish_timestamp', None)
    [StatsNap]
    formatable.field('naps', list())

    @property
    def duration(self):
        if self.created_timestamp and self.finish_timestamp:
            return self.finish_timestamp - self.created_timestamp


class MemoryThreadStatistics(log.Logger):
    '''
    Implementation used in tests.
    '''

    implements(IThreadStatistics)

    def __init__(self, logger):
        log.Logger.__init__(self, logger)
        self.reset()

    def reset(self):
        self.threads = 0
        self.processing = dict()
        self.finished = list()

    ### IThreadStatistics ###

    def new_item(self, job_id, explanation=None):
        self.debug('New item called, explanation=%s', explanation)
        record = StatsRecord(explanation=explanation,
                             created_timestamp=time.time())
        self.processing[job_id] = record

    def started_item(self, job_id):
        record = self.processing.get(job_id)
        if record:
            self.debug('Started item, explanation=%s', record.explanation)
            record.start_timestamp = time.time()

    def fallen_asleep(self, job_id, reason=None):
        record = self.processing.get(job_id)
        if record:
            self.debug('Falled asleep, explanation=%s', record.explanation)
            record.naps.append(StatsNap(reason=reason,
                                        start_timestamp=time.time()))

    def woken_up(self, job_id):
        record = self.processing.get(job_id)
        if record:
            self.debug('Woken up, explanation=%s', record.explanation)
            record.naps[-1].finish_timestamp = time.time()

    def finished_item(self, job_id):
        record = self.processing.pop(job_id, None)
        if record:
            self.debug('Finished item, explanation=%s', record.explanation)
            record.finish_timestamp = time.time()
            self.finished.append(record)

    def new_thread(self):
        self.debug('New thread started')
        self.threads += 1

    def exit_thread(self):
        self.debug('Thread exited')
        self.threads -= 1


class ThreadPoolWithStats(threadpool.ThreadPool, log.Logger):

    def __init__(self, minthreads=5, maxthreads=20, statistics=None,
                 reactor=None, logger=None):
        log.Logger.__init__(self, logger)
        threadpool.ThreadPool.__init__(
            self, minthreads, maxthreads, name="Django")
        self._stats = statistics is not None and IThreadStatistics(statistics)
        if reactor is None:
            from twisted.internet import reactor
        self._reactor = reactor

    def callInThreadWithCallback(self, onResult, func, *args, **kw):
        job_explanation = kw.pop('job_explanation', None) or \
                          reflect.canocial_name(func)
        if not self.joined and self._stats:
            job_id = str(uuid.uuid1())
            self._stats.new_item(job_id, job_explanation)

        kw['__threadpool_job_id'] = job_id
        threadpool.ThreadPool.callInThreadWithCallback(
            self, onResult, func, *args, **kw)

    def deferToThread(self, func, *args, **kw):
        d = defer.Deferred()

        def onResult(success, result):
            if success:
                d.callback(result)
            else:
                d.errback(result)

        self.callInThreadWithCallback(onResult, func, *args, **kw)
        return d

    def _worker(self):
        """
        Method used as target of the created threads: retrieve task to run
        from the threadpool, run it, and proceed to the next task until
        threadpool is stopped.
        """

        def call_stats(method, *args):
            if self._stats:
                self._reactor.callFromThread(
                    getattr(self._stats, method), *args)

        call_stats('new_thread')

        ct = self.currentThread()
        o = self.q.get()
        while o is not threadpool.WorkerStop:
            self.working.append(ct)
            ctx, function, args, kwargs, onResult = o
            job_id = kwargs.pop('__threadpool_job_id')
            del o

            try:
                call_stats('started_item', job_id)
                result = context.call(ctx, function, *args, **kwargs)
                success = True
            except:
                success = False
                if onResult is None:
                    error.handler_failure('thread', failure.Failure(),
                                          'Exception in thread: ')
                    result = None
                else:
                    result = failure.Failure()

            del function, args, kwargs
            call_stats('finished_item', job_id)

            self.working.remove(ct)

            if onResult is not None:
                self._reactor.callFromThread(onResult, success, result)

            del ctx, onResult, result

            self.waiters.append(ct)
            o = self.q.get()
            self.waiters.remove(ct)

        self.threads.remove(ct)
        call_stats('exit_thread')
