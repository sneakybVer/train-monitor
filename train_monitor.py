#!/usr/bin/env python
from consts import *
from suds.client import Client
from suds.sax.element import Element
from twython import Twython, TwythonError
import datetime
import pytz
import time
import collections
import logging
import sys

class Service( object ):
	
	def __init__( self, scheduledTimeStr, station, destination ):
		self.scheduledTimeStr = scheduledTimeStr
		self.scheduledTime    = datetime.datetime.strptime( scheduledTimeStr, '%H:%M' )
		self.station 	      = station
		self.destination      = destination

	def _allProperties( self ):
		return ( self.scheduledTimeStr, self.station, self.destination )

	def serialise( self ):
		return ' '.join( self._allProperties() )

	def printInfo( self ):
		return 'The %s service from %s to %s' % self._allProperties() 

class ServicesMonitor( object ):

	# TODO implement holding onto services for a certain amount of time
	
	def __init__( self, cacheFilePath = '' ):
		self.cacheFilePath = cacheFilePath	

	def _servicesFromFile( self ):
		res = []
		if self.cacheFilePath:
			logging.info( 'loading services from {}'.format( self.cacheFilePath ) )
			with open( self.cacheFilePath, 'r' ) as f:
				content = f.read()
			serialisedServices = content.split( '\n' )
			for s in serialisedServices:
				if s:
					info = s.split( ' ' )
					res.append( Service( info[0], info[1], info[2] ) )
		return res		

	def _saveServicesToFile( self, services ):
		if self.cacheFilePath:
			with open( self.cacheFilePath, 'w' ) as f:
				f.write( '\n'.join( { s.serialise() for s in services } ) )
	
	def _createService( self, info ):
		time        = info[0]
		station	    = info[1]
		destination = info[2]
		return Service( time, station, destination )

	def insertNewServices( self, newServices ):
		cache = self._servicesFromFile()
		cache.extend( [ self._createService( newService.split( ' ' ) ) for newService in newServices ] )
		self._saveServicesToFile( cache )

	def removeServices( self, servicesToRemove ):
		cache = self._servicesFromFile()
		for serviceToRemove in servicesToRemove:
			service = self._createService( serviceToRemove.split( ' ' ) )
			for existingService in cache:
				if service.serialise() == existingService.serialise():
					cache.remove( existingService )
		self._saveServicesToFile( cache )

	def _getCurrentTime( self ):
	        # get current time but remove timezone after
        	now = datetime.datetime.now( pytz.timezone( 'Europe/London' ) )
           	now = now.replace( tzinfo = None )
		return now

	def _isWithinTimeframe( self, scheduledTime ):
		return ( scheduledTime - self._getCurrentTime() ).seconds < 1800

	def getServicesToMonitor( self ):
		return [ service for service in self._servicesFromFile() if self._isWithinTimeframe( service.scheduledTime ) ]


class CommunicationBot( object ):

	def __init__( self ):
		self.twitter = Twython( TW_CONS_KEY, TW_CONS_SECRET, TW_ACCESS_KEY, TW_ACCESS_SECRET ) 
		self.mostRecentMessageId = self._loadMostRecentMessageId()

	def _loadMostRecentMessageId( self ):
		with open( MESSAGE_ID_FILE, 'r' ) as f:
			id = f.read()
			return int( id )
	
	def _isRequiredFormat( self, request ):
		# quite noddy, TODO make smarter, use re
		items = request.split( ' ' )
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
		validRemoveRequests = []
		
		try:
			messages = self.twitter.get_direct_messages( since_id =  self.mostRecentMessageId if self.mostRecentMessageId else None )
		except Exception as e:
			logging.error( 'Error getting direct messages, restarting twitter client' )
			self.twitter = Twython( TW_CONS_KEY, TW_CONS_SECRET, TW_ACCESS_KEY, TW_ACCESS_SECRET ) 
	 	
		if messages:
			for message in messages:
				self.mostRecentMessageId = message.get( 'id' ) if message.get( 'id' ) > self.mostRecentMessageId else self.mostRecentMessageId
				request = message.get( 'text' )

				if 'STOP' in request:
					if self._isRequiredFormat( request.replace( 'STOP', '' ) ):
						logging.info( 'Removing: %s', request )
						validRemoveRequests.append( request )
						continue						

				if self._isRequiredFormat( request ):
					logging.info( 'Subscribing to: %s', request )
					self.postDirectMessage( message.get( 'sender_id' ), 'Subscribed!' )
					validServiceRequests.append( request )
					continue

				self.postDirectMessage( message.get( 'sender_id' ), 'I received an invalid request: %s' % request )
				self.postDirectMessage( message.get( 'sender_id' ), 'Valid message format is HH:MM STN DEST, using station CRS codes. For example, 13:24 HIT KGX' )

			with open( MESSAGE_ID_FILE, 'w' ) as f:
				f.truncate()
				f.write( str( self.mostRecentMessageId ) )
		
		return validServiceRequests, validRemoveRequests

	def _postDirectMessage( self, userId, message ):
		try:
			self.twitter.send_direct_message( user_id = userId, text = message )
		except TwythonError as e:
			logging.warn( 'Twython error sending direct message: %s', e.msg )		

	def _postTweet( self, message ):
		try:
			tweet = datetime.date.today().strftime('%d/%m/%y') + ': ' + message
			self.twitter.update_status( status = tweet )
		except TwythonError as e:
			logging.warn( 'Twython error posting tweet: %s', e.msg )

	def sendMessages( self, messages ):
		for message in messages:
			self._postTweet( message )


class ArrivalETAMonitor( object ):

	def __init__( self, servicesMonitor, communicationClient, interval ):
		self.nationalRailClient = self._setupClient()
		self.servicesClient = servicesMonitor
		self.communicationClient = communicationClient
		self.interval = interval

	def _setupClient( self ):
		token = Element( 'AccessToken', ns = DARWIN_WEBSERVICE_NAMESPACE )
		val = Element( 'TokenValue', ns = DARWIN_WEBSERVICE_NAMESPACE )
		val.setText( DARWIN_TOKEN )
		token.append( val )
		client = Client( LDBWS_URL )
		client.set_options( soapheaders = ( token ) )
		return client

	def _getDesiredServiceFromDepartureBoard( self, service ):
		depBoard = []
		try:
			depBoard = self.nationalRailClient.service.GetDepBoardWithDetails( 10, service.station, service.destination, None, None, None )
		except Exception as e:
			self.nationalRailClient = self._setupClient()
		if depBoard:
			for serviceItem in depBoard.trainServices.service:
				if serviceItem.std == service.scheduledTimeStr:
					for serviceLocation in serviceItem.destination.location:
						if serviceLocation.crs == service.destination:
							return serviceItem

	def _calculateDelay( self, scheduled, estimate ):
		try:
			estimate = datetime.datetime.strptime( estimate, '%H:%M' )
		except Exception as _:
			# this is because 'On Time' is a valid value for etd
			estimate = scheduled
		return estimate - scheduled

	def getNewServiceRequests( self ):
		if self.communicationClient:
			return self.communicationClient.getNewServiceRequests()
		return [], []

	def checkForNewServiceRequests( self ):
		addServiceMessages, removeServiceMessages = self.getNewServiceRequests()
		if removeServiceMessages:
			self.servicesClient.removeServices( removeServiceMessages )
		if addServiceMessages:
			self.servicesClient.insertNewServices( addServiceMessages )			

	def queryServices( self ):			
		delays = []
		for service in self.servicesClient.getServicesToMonitor():
			logging.info( "querying for service: %s", service.printInfo() )

			serviceData = self._getDesiredServiceFromDepartureBoard( service )
			if serviceData:
				delay = self._calculateDelay( service.scheduledTime, serviceData.etd )
				if delay.seconds > ( 3 * 60 ):
					logging.info( "sending delay warning for: %s", service.printInfo() )
					notificationStr = service.printInfo() + ' is delayed by %s minutes' % ( delay.seconds / 60 ) 
					delays.append( notificationStr )
		if self.communicationClient:
			self.communicationClient.sendMessages( delays )
		return delays

	def monitorServices( self ):
		while 1:
			self.checkForNewServiceRequests()
			self.queryServices()
			time.sleep( self.interval )

def setupTrainMonitor( pathToMonitorCache, pathToLogFile, communicationClient, queryInterval ):
	logging.basicConfig( filename = pathToLogFile, level = logging.INFO, format = '%(asctime)s %(message)s', datefmt = '%m/%d/%Y %I:%M:%S %p' )
	logging.info( 'Starting' )
	servicesMonitor = ServicesMonitor( pathToMonitorCache )
	monitor = ArrivalETAMonitor( servicesMonitor, communicationClient, queryInterval )
	return monitor

def run( pathToMonitorCache = '', queryInterval = 120 ):
	communicationClient = CommunicationBot()
	monitor = setupTrainMonitor( pathToMonitorCache, 'train_monitor.log', communicationClient, queryInterval )
	monitor.monitorServices()

def main( args ):
	path = args[0]
	run( path )

if __name__ == '__main__':
	main( sys.argv[1:] )
