import sys
from flask import Flask
from train_monitor import setupTrainMonitor
import logging
from util import retry

sys.path.append("..")

app = Flask(__name__)

logging.basicConfig(filename='/home/steve/stephensbot/server/logs.txt', level=logging.INFO,
                    format='%(asctime)s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')


trainMonitor = None


def getTrainMonitor():
    global trainMonitor
    trainMonitor = setupTrainMonitor('/home/steve/stephensbot/trains_to_monitor.txt',
                                     '/home/steve/stephensbot/server/logs.txt', None, None)


getTrainMonitor()


@app.route("/train_monitor/getDelays")
def getDelays():
    @retry(getTrainMonitor, default='No Services Found')
    def query():
        return trainMonitor.queryServices()

    return str(query())


print "ready"
