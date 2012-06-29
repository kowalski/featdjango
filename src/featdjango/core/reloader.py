import sys

from twisted.internet import task

from django.utils import autoreload


class Reloader(object):

    def __init__(self, reactor, site, period=1):
        self._reactor = reactor
        self._task = None
        self._period = period
        self._site = site

    def run(self):
        self._task = task.LoopingCall(self._inner_run)
        self._task.start(self._period)

    def _inner_run(self):
        if autoreload.code_changed():
            d = self._site.cleanup()
            d.addCallback(sys.exit, autoreload.restart_with_reloader())
            return d
