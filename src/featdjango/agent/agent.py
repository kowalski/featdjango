import os
import sys

from feat.agents.base import agent, replay, descriptor, dependency
from feat.common import error, fiber
from feat.configure import configure
from feat.database import document
from feat.web import security, webserver

from feat.agents.monitor.interface import RestartStrategy
from feat.interface.agency import ExecMode

from featdjango.application import featdjango

from featdjango.agent.interface import IServerFactory


@featdjango.register_descriptor('django_agent')
class Descriptor(descriptor.Descriptor):

    document.field('django_settings_module', None)
    document.field('environment', dict())
    document.field('prefix', None)
    document.field('port', None)
    document.field('hostname', None)
    document.field('interface', u'')
    document.field('elflog_path', None)
    document.field('elflog_fields', None)
    document.field('p12_path', None)
    document.field('check_client_cert', False)
    document.field('python_path', list())


@featdjango.register_agent('django_agent')
class DjangoAgent(agent.Standalone):

    restart_strategy = RestartStrategy.wherever

    dependency.register(IServerFactory, 'featdjango.agent.labour.Simulation',
                        ExecMode.test)
    dependency.register(IServerFactory, 'featdjango.agent.labour.Simulation',
                        ExecMode.simulation)
    dependency.register(IServerFactory, 'featdjango.agent.labour.Production',
                        ExecMode.production)

    @staticmethod
    def get_cmd_line(desc):
        command, args, env = agent.Standalone.get_cmd_line(desc)
        env['DJANGO_SETTINGS_MODULE'] = desc.django_settings_module
        for key, value in desc.environment.items():
            env[key] = str(value)
        return command, args, env

    @replay.mutable
    def initiate(self, state):
        desc = state.medium.get_descriptor()
        for required in ['django_settings_module', 'port']:
            if not getattr(desc, required):
                return fiber.fail(error.FeatError(
                    "Cannot start without required parameter: %r" % required))
        for key, value in desc.environment.items():
            self.info("Setting %s=%r environment variable.", key, value)
            os.environ[key] = str(value)
        for element in reversed(desc.python_path):
            self.info("Injecting %r to the python path.", element)
            sys.path.insert(0, element)

        hostname = desc.hostname or state.medium.get_hostname()
        stats = None
        if desc.elflog_path:
            path = desc.elflog_path
            if not os.path.isabs(path):
                path = os.path.join(configure.logdir, path)
            stats = webserver.ELFLog(path, desc.elflog_fields)
        if desc.p12_path and not os.path.isabs(desc.p12_path):
            p12_path = os.path.join(configure.confdir, desc.p12_path)
        else:
            p12_path = desc.p12_path
        security_policy = None
        if p12_path:
            if not os.path.exists(p12_path):
                return fiber.fail(error.FeatError(
                    "P12 file was specified, but doesn't exist. Path: %s." %
                    (self.p12_path, )))
            fac = security.ServerContextFactory(
                p12_filename=p12_path,
                enforce_cert=desc.check_client_cert,
                verify_ca_from_p12=True)
            security_policy = security.ServerPolicy(fac)

        state.server = self.dependency(IServerFactory,
                                       self, desc.port,
                                       str(desc.django_settings_module),
                                       hostname,
                                       prefix=desc.prefix,
                                       interface=desc.interface,
                                       security_policy=security_policy,
                                       server_stats=stats)
        return fiber.wrap_defer(state.server.initiate)

    @replay.mutable
    def shutdown(self, state):
        self.info("Shutdown called")
        if hasattr(state, 'server'):
            return fiber.wrap_defer(state.server.cleanup)

    @replay.mutable
    def on_killed(self, state):
        if hasattr(state, 'server'):
            self.info("on_killed called")
            return fiber.wrap_defer(state.server.cleanup)
