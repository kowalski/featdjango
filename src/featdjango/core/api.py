from feat.common import decorator, defer, annotate

from feat.models import model, value, getter, action, call
from feat.models.interface import ActionCategories, IModel
from feat.gateway import models

from featdjango.agent import agent
from featdjango.application import featdjango
from featdjango.core import graph


@featdjango.register_model
@featdjango.register_adapter(agent.DjangoAgent, IModel)
class Agent(models.Agent):

    model.identity('featdjango.agent')

    model.child('stats', label='Thread stats',
                source=call.source_call('get_thread_stats'),
                model='featdjango.server.stats')


@featdjango.register_model
class Server(model.Model):

    model.identity('featdjango.server')
    model.child('stats', model='featdjango.server.stats',
                source=getter.source_attr('thread_stats'))


@featdjango.register_model
class Stats(model.Model):

    model.identity('featdjango.server.stats')
    model.child('waiting_times',
                model='featdjango.server.stats.waiting_times',
                desc=("Statistics of waiting for a thread worked to "
                      "start processing the request"))
    model.child('processing_times',
                model='featdjango.server.stats.processing_times',
                desc="Statistics of processing the django views")


@decorator.parametrized_function
def get_graph(method, name, params):

    Action = action.MetaAction.new(
        name, ActionCategories.retrieve,
        effects=[call.model_perform(method.__name__)],
        params=params,
        result_info=value.InterfaceValue(graph.IGraph))

    annotate.injectClassCallback(name, 4, 'annotate_action', name, Action)

    return method


timeparams = [action.Param('start_date', value.Integer(), is_required=False),
              action.Param('end_date', value.Integer(), is_required=False)]


def _parse_time_params(start_date, end_date):
    conditions = []
    params = []
    if start_date:
        conditions.append("start_date >= DATETIME(?, 'unixepoch')")
        params.append(start_date)
    if end_date:
        conditions.append("end_date < DATETIME(?, 'unixepoch')")
        params.append(end_date)
    return conditions, params


@featdjango.register_model
class WaitingTimes(model.Model):

    model.identity('featdjango.server.stats.waiting_times')

    @get_graph('timeline', timeparams)
    def timeline_graph(self, start_date=None, end_date=None):
        d = self._get_waiting_times(start_date, end_date)
        d.addCallback(graph.TimelineGraph)
        return d

    @get_graph('histogram', timeparams)
    def histogram_graph(self, start_date=None, end_date=None):
        d = self._get_waiting_times(start_date, end_date)
        d.addCallback(graph.Histogram, barwidth=0.001)
        return d

    def _get_waiting_times(self, start_date, end_date):
        conditions, params = _parse_time_params(start_date, end_date)
        d = self.source.storage.get_db()
        d.addCallback(
            query,
            'SELECT strftime("%s", created), elapsed from waiting_times',
            conditions, params)
        return d


params = timeparams + [
    action.Param('viewname', value.String(), is_required=False),
    action.Param('method', value.String(), is_required=False),
    ]


def source_item(item):

    def source_item(value, context, *args, **kwargs):
        return context['source'][item]
    return source_item


@featdjango.register_model
class ProcessingTimes(model.Model):

    model.identity('featdjango.server.stats.processing_times.label')
    model.attribute('method', value.String(), source_item(0))
    model.attribute('viewname', value.String(), source_item(1))
    model.attribute('count', value.Integer(), source_item(2))
    model.attribute('min', value.Float(), source_item(3))
    model.attribute('average', value.Float(), source_item(4))
    model.attribute('max', value.Float(), source_item(5))


@featdjango.register_model
class ProcessingTimes(model.Model):

    model.identity('featdjango.server.stats.processing_times')
    model.collection(
        'handlers', child_names=call.model_call('get_handlers'),
        child_source=getter.model_get('lookup_path'),
        child_model="featdjango.server.stats.processing_times.label",
        meta=[('html-render', 'array, 4'),
              ],
        model_meta=[('html-render', 'array-columns, method, viewname, count, '
                     'min, average, max'),
                    ],
        )

    def get_handlers(self):
        d = self.source.storage.get_db()
        d.addCallback(query,
                      'SELECT method, viewname, count(*), avg(elapsed), '
                      'min(elapsed), max(elapsed) '
                      'FROM processed_requests GROUP BY method, viewname;')
        d.addCallback(lambda x: (dict(((r[0], r[1]), tuple(r[2:]))
                                      for r in x)))
        d.addCallback(defer.keep_param, defer.inject_param, 2,
                      setattr, self, '_handlers')
        d.addCallback(lambda x: [" ".join(key) for key in x.keys()])
        return d

    def lookup_path(self, name):
        key = name.split(" ", 1)
        if len(key) != 2:
            return
        if hasattr(self, '_handlers'):
            return (key[0], key[1]) + self._handlers.get(tuple(key), 0)

    @get_graph('timeline', params)
    def timeline_graph(self, method=None, viewname=None,
                       start_date=None, end_date=None):
        d = self._get_processing_times(method, viewname, start_date, end_date)
        title = ''
        if viewname:
            if method:
                title = "%s %s" % (method, viewname)
            else:
                title = viewname
        d.addCallback(graph.TimelineGraph, title)
        return d

    @get_graph('histogram', params)
    def histogram_graph(self, method=None, viewname=None,
                       start_date=None, end_date=None):
        d = self._get_processing_times(method, viewname, start_date, end_date)
        title = ''
        if viewname:
            if method:
                title = "%s %s" % (method, viewname)
            else:
                title = viewname
        d.addCallback(graph.Histogram, title, barwidth=0.05)
        return d

    def _get_processing_times(self, method, viewname, start_date, end_date):
        conditions, params = _parse_time_params(start_date, end_date)
        if viewname:
            conditions.append("viewname == ?")
            params.append(viewname)

        if method:
            conditions.append("method == ?")
            params.append(method)

        d = self.source.storage.get_db()
        d.addCallback(
            query,
            'SELECT strftime("%s", started), elapsed from processed_requests',
            conditions, params)
        return d


def query(db, query, conditions=list(), params=list()):
    if conditions:
        query += " WHERE "
        query += ' AND '.join(conditions)
    return db.runQuery(query, params)
