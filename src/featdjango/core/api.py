from feat.common import decorator, defer, annotate

from feat.models import model, value, effect, getter, action, utils, call
from feat.models.interface import ActionCategories


from featdjango.application import featdjango
from featdjango.core import graph


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
        conditions = []
        params = []
        if start_date:
            conditions.append("start_date >= DATETIME(?, 'unixepoch')")
            params.append(start_date)
        if end_date:
            conditions.append("end_date < DATETIME(?, 'unixepoch')")
            params.append(end_date)
        d = self.source.storage.get_db()
        d.addCallback(
            query,
            'SELECT strftime("%s", created), elapsed from waiting_times',
            conditions, params)
        return d


def query(db, query, conditions, params):
    if conditions:
        query += " WHERE "
        query += ' AND '.join(conditions)
    return db.runQuery(query, params)
