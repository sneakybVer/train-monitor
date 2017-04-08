from suds.client import Client
from suds.sax.element import Element
from twython import Twython
import datetime
import pytz
import time
import collections


class Service( object ):
	
	def __init__( self, scheduledTimeStr, station, destination ):
		self.scheduledTimeStr = scheduledTimeStr
		self.scheduledTime    = datetime.datetime.strptime( scheduledTimeStr, '%H:%M' )
		self.station 	      = station
		self.destination      = destination

	def printInfo( self ):
		return 'The %s service from %s to %s' % ( self.scheduledTimeStr, self.station, self.destination ) 

class ServicesMonitor( object ):
	
	def __init__( self ):
		self.servicesCache = collections.deque()
	
	def insertNewServices( self, newServices ):
		for newService in newServices:
			info = newService.split( ' ' )
			# naive implementation - TODO improve
			time = info[0]
			station = info[1]
			destination = info[2]

			# maintain a maximum of 5 servies for now, FIFO
			self.servicesCache.appendleft( Service( time, station, destination ) )
			if len( self.servicesCache ) == 5:
				self.servicesCache.pop()

	def getServicesToMonitor( self ):
		# TODO improve to load from file if present etc etc
		return self.servicesCache 


class CommunicationBot( object ):

	CONS_KEY 	  = '3tsfuMeATq2KyuaShbPhSk7uE'
	CONS_SECRET       = 'UdZCUzjSh1rBCHf1Y20QMeAyJcuV7zEF98Mapa4PpOXafmNXBc'
	ACCESS_KEY 	  = '848489622913134593-wkcyysuoz9CGMJ5eLnRWxP9krjPli8q'
	ACCESS_SECRET     = 'zxXcQn8Tv7xU1fzRwcrJSYnfqKINpeLR1ZMyPQBtBctQ3'
	MESSAGE_ID_FILE	  = 'message.txt'

	def __init__( self ):
		self.twitter = Twython( self.CONS_KEY, self.CONS_SECRET, self.ACCESS_KEY, self.ACCESS_SECRET ) 
		self.mostRecentMessageId = self._loadMostRecentMessageId()

	def _loadMostRecentMessageId( self ):
		id = ''
		with open( self.MESSAGE_ID_FILE, 'r' ) as f:
			id = f.read()
		return id
	
	def _isRequiredFormat( self, serviceRequest ):
		# quite noddy, TODO make smarter, use re
		items = serviceRequest.split( ' ' )
		if len( items ) == 3:
			try:
				datetime.datetime.strptime( items[0], '%H:%M' )
				if len( items[1] ) == 3 and len( items[2] ) == 3:
					return True
				else:
					return False
			except Exception as _:
				return False

	def getNewServiceRequests( self ):
		validServiceRequests = []
		messages = self.twitter.get_direct_messages( since_id = int( self.mostRecentMessageId ) if self.mostRecentMessageId else None )
		if messages:
			for message in messages:
				self.mostRecentMessageId = str( message.get( 'id' ) )
				serviceRequest = message.get( 'text' )
				if self._isRequiredFormat( serviceRequest ):
					validServiceRequests.append( serviceRequest )
				else:
					self.postTweet( 'I received an invalid request: %s' % message.get( 'text' ) )
					self.postTweet( 'Valid message format is HH:MM STN DEST, using station CRS codes. For example, 13:24 HIT KGX' )

			with open( self.MESSAGE_ID_FILE, 'w' ) as f:
				f.truncate()
				f.write( self.mostRecentMessageId )
		return validServiceRequests

	def postTweet( self, message ):
		self.twitter.update_status( status = message )

class ArrivalETAMonitor( object ):

	DARWIN_WEBSERVICE_NAMESPACE = ( 'com', 'http://thalesgroup.com/RTTI/2010-11-01/ldb/commontypes' )	
	TOKEN = 'c3298d2f-9ac8-43dd-bfb6-ba071609ec01'
	LDBWS_URL = 'https://lite.realtime.nationalrail.co.uk/OpenLDBWS/wsdl.aspx?ver=2016-02-16'

	def __init__( self ):
		self.nationalRailClient = self._setupClient()
		self.servicesClient = ServicesMonitor()
		self.communicationClient = CommunicationBot()

	def _setupClient( self ):
		token = Element( 'AccessToken', ns = self.DARWIN_WEBSERVICE_NAMESPACE )
		val = Element( 'TokenValue', ns = self.DARWIN_WEBSERVICE_NAMESPACE )
		val.setText( self.TOKEN )
		token.append( val )
		client = Client( self.LDBWS_URL )
		client.set_options( soapheaders = ( token ) )
		return client

	def _getDesiredServiceFromDepartureBoard( self, station, destination, scheduledTime ):
		depBoard = self.nationalRailClient.service.GetDepBoardWithDetails( 10, station, destination, None, None, None )
		for serviceItem in depBoard.trainServices.service:
			if serviceItem.std == sheduledTime:
				for serviceLocation in serviceItem.destination.location:
					if serviceLocation.crs == destination:
						return serviceItem

	def _calculateDelay( self, scheduled, estimate ):
		try:
			estimate = datetime.datetime.strptime( service.etd, '%H:%M' )
		except Exception as _:
			# this is because 'On Time' is a valid value for etd
			estimate = scheduled
		return estimate - scheduled

	def _getCurrentTime( self ):
	        # get current time but remove timezone after
        	now = datetime.datetime.now( pytz.timezone( 'Europe/London' ) )
           	now = now.replace( tzinfo = None )
		return now

	def monitorServices( self ):
		while 1:
			# dict of service time to 
			newServiceMessages = self.communicationClient.getNewServiceRequests()
			self.servicesClient.insertNewServices( newServiceMessages )			
			
			for service in self.servicesClient.getServicesToMonitor():
				if ( service.scheduledTime - self._getCurrentTime() ).seconds < ( 1800 ):
					#debug
					print "monitoring service: %s" % service.printInfo()

					serviceData = self._getDesiredServiceFromDepartureBoard()
					if serviceData:
						delay = self._calculateDelay( service.scheduledTime, serviceData.etd )
						if delay.seconds > ( 3 * 60 ):
							#debug
							print "sending delay warning for: %s" % service.printInfo()
							notificationStr = service.printInfo() + ' is delayed by %s minutes' % ( delay.seconds / 60 ) 
							self.communicationClient.postTweet( notificationStr )
			time.sleep( 120 )

def run():
	monitor = ArrivalETAMonitor()
	monitor.monitorServices()
