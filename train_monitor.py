from consts import *
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
				print 'Dropping: %s' % drop.printInfo()

	def getServicesToMonitor( self ):
		# TODO improve to load from file if present etc etc
		return self.servicesCache 


class CommunicationBot( object ):

	def __init__( self ):
		self.twitter = Twython( TW_CONS_KEY, TW_CONS_SECRET, TW_ACCESS_KEY, TW_ACCESS_SECRET ) 
		self.mostRecentMessageId = self._loadMostRecentMessageId()

	def _loadMostRecentMessageId( self ):
		id = ''
		with open( MESSAGE_ID_FILE, 'r' ) as f:
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
		except Exception as e:
			print e		

	def postTweet( self, message ):
		try:
			self.twitter.update_status( status = message )
		except Exception as e:
			print e

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
			if serviceItem.std == service.sheduledTimeStr:
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

					serviceData = self._getDesiredServiceFromDepartureBoard( service )
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
