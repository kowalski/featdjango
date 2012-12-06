import sys

from feat.common import defer
from twisted.internet import task, reactor

from django.utils import autoreload


class Reloader(object):

    def __init__(self, reactor, site, period=1):
        self._reactor = reactor
        self._task = None
        self._period = period
        self._site = site
        self._should_reload = False

    def run(self):
        self._task = task.LoopingCall(self._inner_run)
        self._task.start(self._period)

    def after_stop(self):
        if self._should_reload:
            autoreload.restart_with_reloader()

    def _inner_run(self):
        if autoreload.code_changed():
            self._should_reload = True
            d = self._site.cleanup()
            d.addCallback(defer.drop_param, reactor.stop)
            return d
