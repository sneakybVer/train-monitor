import sys
sys.path.append( ".." )

from flask import Flask
from train_monitor import setupTrainMonitor
import logging

app = Flask( __name__ )

logging.basicConfig( filename = '/home/steve/stephensbot/server/logs.txt', level = logging.INFO, format = '%(asctime)s %(message)s', datefmt = '%m/%d/%Y %I:%M:%S %p' )

trainMonitor = None

def getTrainMonitor():
	global trainMonitor
	trainMonitor = setupTrainMonitor( '/home/steve/stephensbot/trains_to_monitor.txt', '/home/steve/stephensbot/server/logs.txt', None, None )

getTrainMonitor()

def callClassFn( inst, fn, *args, **kwargs ):
	return getattr( inst, fn )( *args, **kwargs )

@app.route( "/train_monitor/getDelays" )
def getDelays():
	@retry( getTrainMonitor, default = 'No Services Found' )
	def query():
		return trainMonitor.queryServices()	
	return str( query() )

def retry( callback, default = None, tries = 2 ):
	def retryDecorator( fn ):
		def funcWrapper( *args, **kwargs ):
			for i in xrange( tries ):
				try:
					return fn( *args, **kwargs )		
				except Exception as e:
					logging.error( e )
					logging.info( 'calling callback {} after error'.format( callback.__name__ ) )
					callback()
					logging.info( 'retrying {} after error'.format( fn.__name__ ) )
			return default
		return funcWrapper				
	return retryDecorator	
		

print "ready"
