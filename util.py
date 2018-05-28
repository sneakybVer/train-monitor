import logging


def retry(callback, default=None, tries=2):
    def retryDecorator(fn):
        def funcWrapper(*args, **kwargs):
            for i in xrange(tries):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    logging.error(e)
                    logging.info('calling callback {} after error'.format(callback.__name__))
                    callback()
                    logging.info('retrying {} after error'.format(fn.__name__))
            return default

        return funcWrapper

    return retryDecorator
