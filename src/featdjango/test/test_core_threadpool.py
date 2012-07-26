import Queue

from feat.common import defer
from feat.test import common

from featdjango.core import threadpool


class ThreadpoolTest(common.TestCase):

    def setUp(self):
        self.stats = threadpool.MemoryThreadStatistics(self)
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

    @defer.inlineCallbacks
    def testDoingAsyncWork(self):
        finish, d = self.do_sync_work('some work')
        self.assertEqual(1, len(self.stats.processing))
        record = self.stats.processing.values()[0]
        self.assertEqual('some work', record.explanation)

        finish(3)
        res = yield d
        self.assertEqual(3, res)

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
        self.assertEqual(less_than_max, len(self.stats.processing))
        yield self.wait_for(lambda : self.stats.threads == less_than_max, 5,
                            freq=0.01)

        # now run some more
        control.extend([self.do_sync_work(str(x))
                        for x in range(less_than_max, more_than_max)])
        self.assertEqual(more_than_max, len(self.stats.processing))
        yield self.wait_for(lambda : self.stats.threads == self.tp.max, 5,
                            freq=0.01)

        # now finish one
        finish, d = control.pop(0)
        finish(None)
        yield d

        self.assertEqual(more_than_max - 1, len(self.stats.processing))
        self.assertEqual(1, len(self.stats.finished))
        self.assertEqual(self.tp.max, self.stats.threads)

        # finish all jobs
        for finish, d in control:
            finish(None)
            yield d

        self.assertEqual(0, len(self.stats.processing))
        self.assertEqual(more_than_max, len(self.stats.finished))
        self.assertEqual(self.tp.max, self.stats.threads)
