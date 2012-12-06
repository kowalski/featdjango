import Queue

from feat.common import defer
from feat.test import common

from featdjango.core import threadpool


class _ThreadpoolTest(common.TestCase):

    def setUp(self):
        self.setup_stats()
        self.tp = threadpool.ThreadPoolWithStats(statistics=self.stats,
                                                 logger=self)
        self.tp.start()
        self.defers = list()

    def tearDown(self):
        self.tp.stop()

    def do_sync_work(self, explanation=None):
        q = Queue.Queue(1)

        def body():
            res = q.get()
            if isinstance(res, Exception):
                raise res
            else:
                return res

        d = self.tp.deferToThread(body, job_explanation=explanation)

        def finish(result):
            q.put(result)

        return finish, d

    def do_async_work(self, result):

        def give_defer(result):
            if isinstance(result, Exception):
                return defer.fail(result)
            else:
                return defer.succeed(result)

        def body():
            return threadpool.blocking_call(give_defer, result)

        d = self.tp.deferToThread(body)
        return d

    @defer.inlineCallbacks
    def testDoingAsyncCallFailsSynchronously(self):

        def call():
            raise AttributeError('blast!')

        def body():
            return threadpool.blocking_call(call)

        d = self.tp.deferToThread(body)
        self.assertFailure(d, AttributeError)
        yield d

    @defer.inlineCallbacks
    def testDoingAsyncCallEndsUpSynchronus(self):

        def call():
            return 5

        def body():
            return threadpool.blocking_call(call)

        r = yield self.tp.deferToThread(body)
        self.assertEqual(5, r)

    @defer.inlineCallbacks
    def testDoingAsyncWork(self):
        res = yield self.do_async_work(4)
        self.assertEqual(4, res)
        if self.stats:
            # check that the stats are processed
            self.assertEqual(1, len(self.stats.finished))
            self.assertEqual(0, len(self.stats.processing))
            record = self.stats.finished[0]
            self.assertEqual(1, len(record.naps))
            self.assertIn('give_defer', record.naps[0].reason)

        ex = AttributeError('attribute')
        d = self.do_async_work(ex)
        self.assertFailure(d, AttributeError)
        yield d
        if self.stats:
            self.assertEqual(2, len(self.stats.finished))
            self.assertEqual(0, len(self.stats.processing))
            record = self.stats.finished[1]
            self.assertEqual(1, len(record.naps))
            self.assertIn('give_defer', record.naps[0].reason)

    @defer.inlineCallbacks
    def testDoingSyncWork(self):
        finish, d = self.do_sync_work('some work')
        if self.stats:
            self.assertEqual(1, len(self.stats.processing))
            record = self.stats.processing.values()[0]
            self.assertEqual('some work', record.explanation)

        finish(3)
        res = yield d
        self.assertEqual(3, res)

        if self.stats:
            # check that we have minthreads thread running
            self.assertEqual(self.tp.min, self.stats.threads)

            # check that the stats are processed
            self.assertEqual(1, len(self.stats.finished))
            self.assertEqual(0, len(self.stats.processing))
            record = self.stats.finished[0]
            self.assertIsNot(None, record.created_timestamp)
            self.assertIsNot(None, record.start_timestamp)
            self.assertIsNot(None, record.finish_timestamp)
            self.assertIsNot(None, record.duration)

    @defer.inlineCallbacks
    def testFailingJob(self):
        finish, d = self.do_sync_work('some work')
        finish(ValueError(3))
        self.assertFailure(d, ValueError)
        yield d

        if self.stats:
            # check that the stats are processed
            self.assertEqual(1, len(self.stats.finished))
            self.assertEqual(0, len(self.stats.processing))
            record = self.stats.finished[0]
            self.assertIsNot(None, record.created_timestamp)
            self.assertIsNot(None, record.start_timestamp)
            self.assertIsNot(None, record.finish_timestamp)
            self.assertIsNot(None, record.duration)

    @defer.inlineCallbacks
    def testMoreJobsThanMax(self):
        more_than_max = self.tp.max + 2
        less_than_max = self.tp.max - 2

        # first run a few
        control = [self.do_sync_work(str(x)) for x in range(less_than_max)]
        if self.stats:
            self.assertEqual(less_than_max, len(self.stats.processing))
            yield self.wait_for(lambda: self.stats.threads == less_than_max, 5,
                                freq=0.01)

        # now run some more
        control.extend([self.do_sync_work(str(x))
                        for x in range(less_than_max, more_than_max)])
        if self.stats:
            self.assertEqual(more_than_max, len(self.stats.processing))
            yield self.wait_for(lambda: self.stats.threads == self.tp.max, 5,
                                freq=0.01)

        # now finish one
        finish, d = control.pop(0)
        finish(None)
        yield d

        if self.stats:
            self.assertEqual(more_than_max - 1, len(self.stats.processing))
            self.assertEqual(1, len(self.stats.finished))
            self.assertEqual(self.tp.max, self.stats.threads)

        # finish all jobs
        for finish, d in control:
            finish(None)
            yield d

        if self.stats:
            self.assertEqual(0, len(self.stats.processing))
            self.assertEqual(more_than_max, len(self.stats.finished))
            self.assertEqual(self.tp.max, self.stats.threads)


class TestWithoutStats(_ThreadpoolTest):

    def setup_stats(self):
        self.stats = None


class TestWithStats(_ThreadpoolTest):

    def setup_stats(self):
        self.stats = threadpool.MemoryThreadStatistics(self)
