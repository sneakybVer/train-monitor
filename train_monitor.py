#!/usr/bin/env python
from consts import *
from suds.client import Client
from suds.sax.element import Element
from twython import Twython, TwythonError
import datetime
import pytz
import time
import logging
import sys
from util import retry


class Service(object):

    def __init__(self, scheduledTimeStr, station, destination):
        self.scheduledTimeStr = scheduledTimeStr
        self.scheduledTime = datetime.datetime.strptime(scheduledTimeStr, '%H:%M')
        self.station = station
        self.destination = destination

    def __str__(self):
        return ' '.join((self.scheduledTimeStr, self.station, self.destination))

    def printInfo(self):
        return 'The %s service from %s to %s' % (self.scheduledTimeStr, self.station, self.destination)


class ServicesMonitor(object):

    # TODO implement holding onto services for a certain amount of time

    def __init__(self, cacheFilePath='', serviceTimeframe=1800):
        self.cacheFilePath = cacheFilePath
        self._serviceTimeframe = serviceTimeframe
        self._services = self._servicesFromFile()

    def _servicesFromFile(self):
        res = []
        if self.cacheFilePath:
            logging.info('loading services from {}'.format(self.cacheFilePath))
            with open(self.cacheFilePath, 'r') as f:
                content = f.read()
            serialisedServices = content.split('\n')
            for s in serialisedServices:
                if s:
                    info = s.split(' ')
                    res.append(Service(info[0], info[1], info[2]))
        return res

    def _saveServicesToFile(self):
        if self.cacheFilePath:
            with open(self.cacheFilePath, 'w') as f:
                f.write('\n'.join({str(s) for s in self._services}))

    @staticmethod
    def _createService(info):
        serviceTime = info[0]
        station = info[1]
        destination = info[2]
        return Service(serviceTime, station, destination)

    def addNewServicesToCache(self, newServices):
        self._services.extend([self._createService(newService.split(' ')) for newService in newServices])

    def addNewServicesToStore(self, newServices):
        self.addNewServicesToCache(newServices)
        self._saveServicesToFile()

    def removeServicesFromCache(self, servicesToRemove):
        for serviceToRemove in servicesToRemove:
            service = self._createService(serviceToRemove.split(' '))
            for existingService in self._services:
                if str(service) == str(existingService):
                    self._services.remove(existingService)

    def removeServicesFromStore(self, servicesToRemove):
        self.removeServicesFromCache(servicesToRemove)
        self._saveServicesToFile()

    def _isWithinTimeframe(self, scheduledTime):
        now = datetime.datetime.now(pytz.timezone('Europe/London'))
        now = now.replace(tzinfo=None)
        return (scheduledTime - now).seconds < self._serviceTimeframe

    def getServicesToMonitor(self):
        return [service for service in self._services if self._isWithinTimeframe(service.scheduledTime)]


class AbstractCommunicationClient(object):

    def getNewServiceRequests(self):
        return [], []

    def sendMessages(self, messages):
        for message in messages:
            self._sendMessage(message)


class TwitterCommunicationBot(AbstractCommunicationClient):

    def __init__(self):
        self._setupTwitter()
        self.mostRecentMessageId = self._loadMostRecentMessageId()
        self.sentMessages = []

    def _setupTwitter(self):
        logging.info('Setting up twitter client')
        self.twitter = Twython(TW_CONS_KEY, TW_CONS_SECRET, TW_ACCESS_KEY, TW_ACCESS_SECRET)

    def _loadMostRecentMessageId(self):
        try:
            with open(MESSAGE_ID_FILE, 'r') as f:
                id = f.read()
                return int(id)
        except (ValueError, IOError) as e:
            logging.warn('Failed to read most recent message, ex %s', e.message)

    def _isRequiredFormat(self, request):
        items = request.split(' ')
        if len(items) == 3:
            if len(items[1]) == 3 and len(items[2]) == 3:
                try:
                    datetime.datetime.strptime(items[0], '%H:%M')
                    return True
                except Exception as _:
                    pass
        return False

    def getNewServiceRequests(self):
        validServiceRequests = []
        validRemoveRequests = []

        @retry(self._setupTwitter)
        def _getMessages():
            return self.twitter.get_direct_messages(since_id=self.mostRecentMessageId
                                                            if self.mostRecentMessageId else None)

        messages = _getMessages() or []

        for message in messages:
            self.mostRecentMessageId = message.get('id') if message.get('id') > (
                    self.mostRecentMessageId or 0) else self.mostRecentMessageId
            request = message.get('text')

            if 'STOP' in request:
                request = request.replace('STOP', '').strip()
                if self._isRequiredFormat(request):
                    logging.info('Removing: %s', request)
                    self._postDirectMessage(message.get('sender_id'), 'Removed! - %s' % request)
                    validRemoveRequests.append(request)
                    continue

            if self._isRequiredFormat(request):
                logging.info('Subscribing to: %s', request)
                self._postDirectMessage(message.get('sender_id'), 'Subscribed! - %s' % request)
                validServiceRequests.append(request)
                continue

            self._postDirectMessage(message.get('sender_id'), 'I received an invalid request: %s' % request)
            self._postDirectMessage(message.get('sender_id'),
                                    'Valid message format is HH:MM STN DEST, using station CRS codes. For example, 13:24 HIT KGX')

        with open(MESSAGE_ID_FILE, 'w') as f:
            f.truncate()
            f.write(str(self.mostRecentMessageId))

        return validServiceRequests, validRemoveRequests

    def _postDirectMessage(self, userId, message):
        try:
            self.twitter.send_direct_message(user_id=userId, text=message)
        except TwythonError as e:
            logging.warn('Twython error sending direct message: %s', e.msg)

    def _sendMessage(self, message):
        tweet = datetime.date.today().strftime('%d/%m/%y') + ': ' + message
        if tweet not in self.sentMessages:
            try:
                self.twitter.update_status(status=tweet)
                self.sentMessages.append(tweet)
            except TwythonError as e:
                logging.warn('Twython error posting tweet: %s', e.msg)
        else:
            logging.warn('Message already sent: %s', tweet)


class ArrivalETAMonitor(object):

    def __init__(self, servicesMonitor, communicationClient=None, interval=None):
        # type: (ServicesMonitor, AbstractCommunicationClient, int) -> None
        self._setupNationalRailClient()
        self.servicesClient = servicesMonitor
        self.communicationClient = communicationClient or AbstractCommunicationClient()
        self.interval = interval or 120

    def _setupNationalRailClient(self):
        logging.info('Setting up national rail client')
        token = Element('AccessToken', ns=DARWIN_WEBSERVICE_NAMESPACE)
        val = Element('TokenValue', ns=DARWIN_WEBSERVICE_NAMESPACE)
        val.setText(DARWIN_TOKEN)
        token.append(val)
        client = Client(LDBWS_URL)
        client.set_options(soapheaders=token)
        self.nationalRailClient = client

    def _getDesiredServiceFromDepartureBoard(self, service):

        @retry(self._setupNationalRailClient)
        def _queryNationalRail():
            depBoard = self.nationalRailClient.service.GetDepBoardWithDetails(10, service.station, service.destination,
                                                                              None, None, None)
            if depBoard:
                for serviceItem in depBoard.trainServices.service:
                    if serviceItem.std == service.scheduledTimeStr:
                        for serviceLocation in serviceItem.destination.location:
                            if serviceLocation.crs == service.destination:
                                return serviceItem

        return _queryNationalRail()

    def _calculateDelay(self, scheduled, estimate):
        try:
            estimate = datetime.datetime.strptime(estimate, '%H:%M')
        except ValueError as _:
            # this is because 'On Time' is a valid value for etd
            estimate = scheduled
        return estimate - scheduled

    def getNewServiceRequests(self):
        return self.communicationClient.getNewServiceRequests()

    def checkForNewServiceRequests(self):
        addServiceMessages, removeServiceMessages = self.getNewServiceRequests()
        if removeServiceMessages:
            logging.info('Found remove service messages %s', removeServiceMessages)
            self.servicesClient.removeServicesFromStore(removeServiceMessages)
        if addServiceMessages:
            logging.info('Found add service messages %s', addServiceMessages)
            self.servicesClient.addNewServicesToStore(addServiceMessages)

    def queryServices(self):
        delays = []
        for service in self.servicesClient.getServicesToMonitor():
            logging.info("querying for service: %s", service.printInfo())

            serviceData = self._getDesiredServiceFromDepartureBoard(service)
            if serviceData:
                if serviceData.etd == "Cancelled":
                    logging.info("sending cancelled warning for: %s", service.printInfo())
                    notificationStr = service.printInfo() + ' is cancelled!'
                    delays.append(notificationStr)
                    self.servicesClient.removeServicesFromCache([str(service)])
                elif serviceData.etd == "Delayed":
                    logging.info("sending delayed warning for: %s", service.printInfo())
                    notificationStr = service.printInfo() + ' is delayed with no ETA'
                    delays.append(notificationStr)
                else:
                    delay = self._calculateDelay(service.scheduledTime, serviceData.etd)
                    if delay.seconds > (3 * 60):
                        logging.info("sending delay warning for: %s", service.printInfo())
                        notificationStr = service.printInfo() + ' is delayed by %s minutes' % (delay.seconds / 60)
                        delays.append(notificationStr)
        self.communicationClient.sendMessages(delays)
        return delays

    def monitorServices(self):
        while 1:
            self.checkForNewServiceRequests()
            self.queryServices()
            time.sleep(self.interval)


def setupTrainMonitor(pathToMonitorCache, pathToLogFile, communicationClient, queryInterval):
    logging.basicConfig(filename=pathToLogFile, level=logging.INFO, format='%(asctime)s %(message)s',
                        datefmt='%m/%d/%Y %I:%M:%S %p')
    logging.info('Starting')
    servicesMonitor = ServicesMonitor(pathToMonitorCache)
    monitor = ArrivalETAMonitor(servicesMonitor, communicationClient, queryInterval)
    return monitor


def run(pathToMonitorCache='', queryInterval=120):
    communicationClient = TwitterCommunicationBot()
    monitor = setupTrainMonitor(pathToMonitorCache, 'train_monitor.log', communicationClient, queryInterval)
    monitor.monitorServices()


def main(args):
    path = args[0]
    run(path)


if __name__ == '__main__':
    main(sys.argv[1:])
