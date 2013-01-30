import ctypes
import Queue
import threading
import time

from zope.interface import Interface, implements

from feat.common import reflect, formatable, log, defer

from twisted.python import context, failure


def blocking_call(_method, *args, **kwargs):
    return blocking_call_ex(_method, args, kwargs)


def blocking_call_ex(method, args, kwargs):
    ct = threading.current_thread()
    if hasattr(ct, 'wait_for_defer'):
        return ct.wait_for_defer(method, args, kwargs)
    else:
        return method(*args, **kwargs)


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


class Thread(threading.Thread):

    def wait_for_defer(self, method, args=tuple(), kwargs=dict()):
        if self._softly_cancelled == self.job_id:
            raise defer.CancelledError()

        self.reactor.callFromThread(
            self._blocking_call_body, method, args, kwargs)
        self.call_stats('fallen_asleep', reflect.canonical_name(method))
        res = self._defer_queue.get()
        self.call_stats('woken_up')

        if isinstance(res, Exception):
            raise res
        else:
            return res

    def run(self):
        try:
            self._defer_queue = Queue.Queue(0)
            self.stats = None
            self.reactor = None
            self.job_id = None
            self._softly_cancelled = None
            super(Thread, self).run()
        finally:
            del self._defer_queue
            del self.stats
            del self.reactor
            del self.job_id
            del self._softly_cancelled

    def call_stats(self, method, *args, **kwargs):
        if self.stats:
            self.reactor.callFromThread(
                getattr(self.stats, method), self.job_id, *args, **kwargs)

    def cancel_softly(self, job_id):
        if self.job_id == job_id:
            self._softly_cancelled = job_id

    ### private ###

    def _blocking_call_body(self, method, args, kwargs):
        try:
            r = method(*args, **kwargs)
        except Exception as e:
            self._blocking_call_cb(e)
            return

        if not isinstance(r, defer.Deferred):
            self._blocking_call_cb(r)
        else:
            r.addBoth(self._blocking_call_cb)
            return r

    def _blocking_call_cb(self, result):
        if isinstance(result, failure.Failure):
            self._defer_queue.put(result.value)
        else:
            self._defer_queue.put(result)


WorkerStop = object()


class ThreadPoolError(Exception):
    pass


class JobCallback(object):
    """
    I'm a layer glueing up the Deferred interface of twisted with the
    specifics of the Threadpool.
    """

    def __init__(self, threadpool, job_id):
        self.threadpool = threadpool
        self.job_id = job_id
        self.deferred = defer.Deferred(canceller=self._cancelled)

    def __call__(self, success, result):
        reactor = self.threadpool._reactor
        reactor.callFromThread(self._trigger, success, result)

    def _trigger(self, success, result):
        if self.deferred.called:
            return
        if success:
            self.deferred.callback(result)
        else:
            self.deferred.errback(result)

    def _cancelled(self, deferred):
        self.threadpool.cancel_job(self.job_id)


class ThreadPool(log.Logger):
    """
    This implementation is inspired by twisted.python.threadpool.ThreadPool.
    Comparing to the original it offers:
    - the possibility to log the statistics to the external object
    - threaded code can call method returning Deferred and wait for the
      result synchronously
    - jobs done in threads are cancellable. When the Deferred returned by
      deferToThread is cancelled, the thread has limitted time to finish
      his job, until it gets killed violently (kill_delay parameter)
    - possibility to have a callable run in the thread context on its
      initialization
    """

    def __init__(self, minthreads=5, maxthreads=20, statistics=None,
                 reactor=None, logger=None, init_thread=None,
                 kill_delay=10, kill_retry_limit=3):
        log.Logger.__init__(self, logger)
        self._init_thread = init_thread
        self.q = Queue.Queue(0)
        self.min = minthreads
        self.max = maxthreads
        self.name = "Django"
        self.threads = []
        self.busy = 0
        self.workers = 0

        # cancel_job() sometimes needs to perform the cleanup of the
        # enqueued tasks. While this is done, calling q.get() is prohibitted
        self._can_queue_get = threading.Event()
        self._can_queue_get.set()

        # how much time to give a job to finish nicely
        # killing it by lethal injections
        self.kill_delay = kill_delay
        # how many times try to retry the death sentence before
        # admitting defeat
        self.kill_retry_limit = kill_retry_limit

        # job-id -> IDelayedCall calls to make sure that some call has finished
        self._kill_laters = dict()

        self.joined = False
        self.started = False

        self._stats = None
        # counter used for generating job_ids
        self._job_index = -1

        # Notifier is used during threadpool termination to know when all
        # the jobs are done without blocking the reactor to wait for them
        self._notifier = defer.Notifier()

        if statistics:
            self._stats = IThreadStatistics(statistics)

        if reactor is None:
            from twisted.internet import reactor
        self._reactor = reactor

    def defer_to_thread(self, func, *args, **kw):
        if self.joined:
            return defer.fail(ThreadPoolError("Pool already stopped"))

        job_explanation = kw.pop('job_explanation', None) or \
                          reflect.canonical_name(func)
        job_id = self._next_job_id(job_explanation)
        cb = JobCallback(self, job_id)
        if self._stats:
            self._stats.new_item(job_id, job_explanation)
        ctx = context.theContextTracker.currentContext().contexts[-1]

        self.q.put((ctx, func, args, kw, cb, job_id))

        if self.started:
            self._start_some_workers()

        return cb.deferred

    def start(self):
        """
        Start the threadpool.
        """
        self.joined = False
        self.started = True
        # Start some threads.
        self._adjust_poolsize()

    def stop(self):
        """
        Shutdown the threads in the threadpool.
        """
        self.joined = True

        while self.workers:
            self.q.put(WorkerStop)
            self.workers -= 1

        for thread in self.threads:
            # gettattr is used, because thread might not have the attribute
            # defined yet, if he is just starting up
            job_id = getattr(thread, 'job_id', None)
            if job_id:
                self.cancel_job(job_id)

        if self.threads:
            d = self._notifier.wait('threads_joined')
            d.addCallback(defer.drop_param, self._cancel_delayed_calls)
            return d
        else:
            self._cancel_delayed_calls()
            return defer.succeed(None)

    ### managing the workers ###

    def start_a_worker(self):
        self.workers += 1
        name = "%s-%s" % (self.name or id(self), self.workers)
        new_thread = Thread(target=self._worker, name=name)
        self.threads.append(new_thread)
        new_thread.start()

    def stop_a_worker(self):
        self.q.put(WorkerStop)
        self.workers -= 1

    ### canceling and killing the jobs ###

    def cancel_job(self, job_id):
        '''
        Locates the thread perforing the job and informs it, it needs to
        cancel. This method tries to be nice at first, the thread is given
        the time to finish on his own. Howether any call to wait_for_defer()
        done by the task would fail right-away.

        After the time period the indent is made to inject the Exception
        into the thread context using ctypes api. This is retried 3 times
        until we can just log a message for a sysadmin to use kill -9 against
        the process.
        '''
        thread = self._thread_by_job_id(job_id)
        if not thread:
            self.debug("Job %s it not being proccessed, I will check if its "
                       "not enqueued", job_id)
            self._can_queue_get.clear()
            try:
                fetched = list()
                while True:
                    try:
                        item = self.q.get_nowait()
                        if isinstance(item, tuple) and item[5] == job_id:
                            self.debug("Job %s was taken out of the queue.",
                                       job_id)
                            item[4].deferred.cancel()
                            break
                        fetched.append(item)
                    except Queue.Empty:
                        break
                for item in reversed(fetched):
                    self.q.put(item)
            finally:
                self._can_queue_get.set()
        else:
            self.debug("Cancelling job %s softly. It will be given %s seconds "
                       "to finish or yield the control to the main thread.",
                       job_id, self.kill_delay)
            thread.cancel_softly(job_id)
            self.kill_later(self.kill_delay, job_id)

    def kill_later(self, delay, job_id, *args):
        if job_id not in self._kill_laters:
            dc = self._reactor.callLater(delay, self.kill_job, job_id, *args)
            self._kill_laters[job_id] = dc

    def kill_job(self, job_id, retry=0):
        dc = self._kill_laters.pop(job_id, None)
        if dc and dc.active():
            dc.cancel()

        thread = self._thread_by_job_id(job_id)
        if not thread:
            self.debug("Killing job %s is not necessary, it has terminated "
                       "nicely.", job_id)
            return
        self.warning("Trying to kill job with id %s.", job_id)
        tid = thread.ident
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            tid, ctypes.py_object(ThreadPoolError))
        if res == 1:
            self.info("PyThreadState_SetAsyncExc succeed")
            return
        elif res == 0:
            self.error("invalid thread id")
        elif res != 1:
            # "if it returns a number greater than one, you're in trouble,
            # and you should call it again with exc=NULL to revert the effect"
            ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, 0)
            self.error("PyThreadState_SetAsyncExc failed")
        if retry < self.kill_retry_limit:
            delay = float(self.kill_delay) / (retry + 1)
            self.info("I will retry the kill in %s seconds.", delay)
            self.kill_later(delay, job_id, retry=retry + 1)
        else:
            self.error("I to kill the job %s %d times, but it won't terminate."
                       "Most likely someone should kill -9 this process now.",
                       job_id, retry + 1)
            return

    ### private ###

    def _adjust_poolsize(self, minthreads=None, maxthreads=None):
        if minthreads is None:
            minthreads = self.min
        if maxthreads is None:
            maxthreads = self.max

        assert minthreads >= 0, 'minimum is negative'
        assert minthreads <= maxthreads, 'minimum is greater than maximum'

        self.min = minthreads
        self.max = maxthreads
        if not self.started:
            return

        # Kill of some threads if we have too many.
        while self.workers > self.max:
            self.stop_a_worker()
        # Start some threads if we have too few.
        while self.workers < self.min:
            self.start_a_worker()
        # Start some threads if there is a need.
        self._start_some_workers()

    def _start_some_workers(self):
        needed = self.q.qsize() + self.busy
        # Create enough, but not too many
        while self.workers < min(self.max, needed):
            self.start_a_worker()

    def _next_job_id(self, job_explanation):
        self._job_index += 1
        return "%s-%s" % (self._job_index, job_explanation)

    def _cancel_delayed_calls(self):
        for x in self._kill_laters.itervalues():
            if x.active():
                x.cancel()
        self._kill_laters.clear()

    def _thread_by_job_id(self, job_id):
        for thread in self.threads:
            if getattr(thread, 'job_id', None) == job_id:
                return thread

    def _thread_finished(self, thread):
        self.threads.remove(thread)
        thread.join()
        if not self.threads:
            self._notifier.callback('threads_joined', None)

    ### worker body ###

    def _worker(self):
        """
        Method used as target of the created threads: retrieve task to run
        from the threadpool, run it, and proceed to the next task until
        threadpool is stopped.
        """
        ct = threading.current_thread()
        ct.reactor = self._reactor
        ct.stats = self._stats
        ct.job_id = None
        if self._stats:
            self._stats.new_thread()

        if callable(self._init_thread):
            self._init_thread()

        self._can_queue_get.wait()
        o = self.q.get()
        while o is not WorkerStop:
            self.busy += 1

            ctx, function, args, kwargs, callback, job_id = o
            del o
            ct.job_id = job_id

            try:
                ct.call_stats('started_item')
                result = context.call(ctx, function, *args, **kwargs)
                success = True
            except:
                success = False
                result = failure.Failure()

            del function, args, kwargs, job_id

            ct.call_stats('finished_item')
            self.busy -= 1
            ct.job_id = None

            callback(success, result)

            del ctx, callback, result

            self._can_queue_get.wait()
            o = self.q.get()

        if self._stats:
            self._stats.exit_thread()
        self._reactor.callFromThread(self._thread_finished, ct)
