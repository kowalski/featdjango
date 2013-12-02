import json
import subprocess

from zope.interface import Interface, implements, Attribute

from feat.web import document
from feat.common import log
from feat.common.text_helper import format_block


class IGraph(Interface):

    data = Attribute("Raw data the graph is constructed from")

    def to_gnuplot():
        '''
        @returns: C{str} script to use to generate a plot.
        '''


base_settings = format_block('''
    set term png transparent truecolor enhanced font "Tahoma,12"
    set style fill transparent solid 0.5
    set border lc rgb "%(base_color)s"
    set xlabel tc rgb "%(base_color)s"
    set ylabel tc rgb "%(base_color)s"
    set key tc rgb "%(base_color)s"

    ''' % dict(base_color="#aaa190"))


class TimelineGraph(object):

    implements(IGraph)

    def __init__(self, data, title=''):
        '''
        Data should be list of tuples (epoch, time)
        '''
        self.data = data
        self.title = title or ''

    def to_gnuplot(self):
        script = base_settings + format_block('''
        set xlabel "Date"
        set xdata time
        set timefmt "%%s"
        set xtics rotate by 90 offset 0,-5 out nomirror
        set format x "%%m/%%d %%H:%%M"

        set ylabel "Waiting time (s)"
        set title "%(title)s"
        plot "-" using 1:2 w point title "Waiting time"
        ''' % dict(title=self.title))

        return format_data(script, self.data)


class Histogram(object):

    implements(IGraph)

    def __init__(self, data, title='', barwidth=1):
        '''
        Data should be list of tuples (epoch, time)
        '''
        self.data = data
        self.title = title or ''
        self.barwidth = barwidth

    def to_gnuplot(self):
        script = base_settings + format_block('''
        set xlabel "Waiting time"
        box(time)=(int(time / %(barwidth)s) * %(barwidth)s)
        set boxwidth %(barwidth)s

        set ylabel "Events in range"
        set title "%(title)s"
        plot "-" using (box($2)):(1.0) smooth freq with boxes
        ''' % dict(title=self.title, barwidth=self.barwidth))

        return format_data(script, self.data)


def format_data(header, data, number_of_plots=1):
    stdin = str(header)
    for x in range(number_of_plots):
        for row in data:
            if isinstance(row, (list, tuple)):
                stdin += " ".join(str(x) for x in row)
            else:
                stdin += str(row)
            stdin += '\n'
        stdin += 'e\n'
    return stdin


def get_png(script):
    gnuplot = subprocess.Popen(['gnuplot'], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    output = gnuplot.communicate(script)
    if output[1]:
        log.error('gnuplot', "Failed to generate the graph. "
                  "Error returned by gnuplot: \n%s", output[1])
    return output[0]


def write_graph(doc, obj, *args, **kwargs):
    doc.write(get_png(obj.to_gnuplot()))


def write_data(doc, obj, *args, **kwargs):
    doc.write(json.dumps(obj.data))


def write_script(doc, obj, *args, **kwargs):
    doc.write(obj.to_gnuplot())


document.register_writer(write_graph, 'image/png', IGraph)
document.register_writer(write_data, 'application/json', IGraph)
document.register_writer(write_script, 'application/gnuplot', IGraph)
