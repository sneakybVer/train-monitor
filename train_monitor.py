from consts import *
from suds.client import Client
from suds.sax.element import Element
from twython import Twython, TwythonError
import datetime
import pytz
import time
import collections
import logging

class Service( object ):
	
	def __init__( self, scheduledTimeStr, station, destination ):
		self.scheduledTimeStr = scheduledTimeStr
		self.scheduledTime    = datetime.datetime.strptime( scheduledTimeStr, '%H:%M' )
		self.station 	      = station
		self.destination      = destination

	def printInfo( self ):
		return 'The %s service from %s to %s' % ( self.scheduledTimeStr, self.station, self.destination ) 

class ServicesMonitor( object ):

	# TODO implement holding onto services for a certain amount of time
	
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
				drop = self.servicesCache.pop()
				logging.info( 'Dropping: %s', drop.printInfo() )

	def getServicesToMonitor( self ):
		# TODO improve to load from file if present etc etc
		return self.servicesCache 


class CommunicationBot( object ):

	def __init__( self ):
		self.twitter = Twython( TW_CONS_KEY, TW_CONS_SECRET, TW_ACCESS_KEY, TW_ACCESS_SECRET ) 
		self.mostRecentMessageId = self._loadMostRecentMessageId()

	def _loadMostRecentMessageId( self ):
		with open( MESSAGE_ID_FILE, 'r' ) as f:
			return f.read()
	
	def _isRequiredFormat( self, serviceRequest ):
		# quite noddy, TODO make smarter, use re
		items = serviceRequest.split( ' ' )
		if len( items ) == 3:
			if len( items[1] ) == 3 and len( items[2] ) == 3:
				try:
					datetime.datetime.strptime( items[0], '%H:%M' )
					return True
				except Exception as _:
					pass
		return False

	def getNewServiceRequests( self ):
		validServiceRequests = []
		messages = self.twitter.get_direct_messages( since_id = int( self.mostRecentMessageId ) if self.mostRecentMessageId else None )
		if messages:
			for message in messages:
				self.mostRecentMessageId = str( message.get( 'id' ) )
				serviceRequest = message.get( 'text' )
				if self._isRequiredFormat( serviceRequest ):
					logging.info( 'Subscribing to: %s', serviceRequest )
					self.postDirectMessage( message.get( 'sender_id' ), 'Subscribed!' )
					validServiceRequests.append( serviceRequest )
				else:
					self.postDirectMessage( message.get( 'sender_id' ), 'I received an invalid request: %s' % message.get( 'text' ) )
					self.postDirectMessage( message.get( 'sender_id' ), 'Valid message format is HH:MM STN DEST, using station CRS codes. For example, 13:24 HIT KGX' )

			with open( MESSAGE_ID_FILE, 'w' ) as f:
				f.truncate()
				f.write( self.mostRecentMessageId )
		return validServiceRequests

	def postDirectMessage( self, userId, message ):
		try:
			self.twitter.send_direct_message( user_id = userId, text = message )
		except TwythonError as e:
			logging.warn( 'Twython error sending direct message: %s', e.msg )		

	def postTweet( self, message ):
		try:
			self.twitter.update_status( status = message )
		except TwythonError as e:
			logging.warn( 'Twython error posting tweet: %s', e.msg )

class ArrivalETAMonitor( object ):

	def __init__( self ):
		self.nationalRailClient = self._setupClient()
		self.servicesClient = ServicesMonitor()
		self.communicationClient = CommunicationBot()

	def _setupClient( self ):
		token = Element( 'AccessToken', ns = DARWIN_WEBSERVICE_NAMESPACE )
		val = Element( 'TokenValue', ns = DARWIN_WEBSERVICE_NAMESPACE )
		val.setText( DARWIN_TOKEN )
		token.append( val )
		client = Client( LDBWS_URL )
		client.set_options( soapheaders = ( token ) )
		return client

	def _getDesiredServiceFromDepartureBoard( self, service ):
		depBoard = self.nationalRailClient.service.GetDepBoardWithDetails( 10, service.station, service.destination, None, None, None )
		for serviceItem in depBoard.trainServices.service:
			if serviceItem.std == service.scheduledTimeStr:
				for serviceLocation in serviceItem.destination.location:
					if serviceLocation.crs == service.destination:
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
			newServiceMessages = self.communicationClient.getNewServiceRequests()
			self.servicesClient.insertNewServices( newServiceMessages )			
			
			for service in self.servicesClient.getServicesToMonitor():
				if ( service.scheduledTime - self._getCurrentTime() ).seconds < ( 1800 ):
					logging.info( "monitoring service: %s", service.printInfo() )

					serviceData = self._getDesiredServiceFromDepartureBoard( service )
					if serviceData:
						delay = self._calculateDelay( service.scheduledTime, serviceData.etd )
						if delay.seconds > ( 3 * 60 ):
							logging.info( "sending delay warning for: %s", service.printInfo() )
							notificationStr = service.printInfo() + ' is delayed by %s minutes' % ( delay.seconds / 60 ) 
							self.communicationClient.postTweet( notificationStr )
			time.sleep( 120 )

def run():
	logging.basicConfig( filename = 'train_monitor.log', level = logging.DEBUG )
	monitor = ArrivalETAMonitor()
	monitor.monitorServices()
