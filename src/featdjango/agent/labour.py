from zope.interface import implements, classProvides

from feat.agents.base import labour
from feat.common import defer

from featdjango.application import featdjango
from featdjango.core import server

from featdjango.agent.interface import IServerFactory, IServer


@featdjango.register_restorator
class Production(labour.BaseLabour, server.Server):
    classProvides(IServerFactory)
    implements(IServer)

    def __init__(self, log_keeper, port, settings_module,
                 host_name=None,
                 prefix=None, interface='',
                 security_policy=None,
                 server_stats=None):

        from django.utils import importlib
        from django.core import management
        module = importlib.import_module(settings_module)
        management.setup_environ(module)

        server.Server.__init__(self, host_name, port, log_keeper=log_keeper,
                               prefix=prefix, web_statistics=server_stats,
                               security_policy=security_policy,
                               interface=interface)
        labour.BaseLabour.__init__(self, log_keeper)

    def cleanup(self):
        return server.Server.cleanup(self)


@featdjango.register_restorator
class Simulation(labour.BaseLabour):
    classProvides(IServerFactory)
    implements(IServer)

    def __init__(self, log_keeper, port, settings_module,
                 host_name=None,
                 prefix=None, interface='',
                 security_policy=None,
                 server_stats=None):
        pass

    def initiate(self):
        return defer.succeed(None)

    def cleanup(self):
        return defer.succeed(None)
