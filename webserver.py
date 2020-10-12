import argparse
import asyncio
import logging
import os
import warnings
import sys

import toml
from aiohttp.web import Application, AppRunner, TCPSite
from aiohttp_jinja2 import setup as jinja_setup
from aiohttp_session import setup as session_setup
from aiohttp_session.cookie_storage import EncryptedCookieStorage as ECS
from jinja2 import FileSystemLoader

import website
import utils

# Set up loggers
logging.basicConfig(format='%(asctime)s:%(name)s:%(levelname)s: %(message)s', stream=sys.stdout)
logger = logging.getLogger(os.getcwd().split(os.sep)[-1].split()[-1].lower())
logger.setLevel(logging.INFO)

# Filter warnings
warnings.filterwarnings('ignore', category=RuntimeWarning)

# Parse arguments
parser = argparse.ArgumentParser()
parser.add_argument("config_file", help="The configuration for the webserver.")
parser.add_argument("--host", type=str, default='0.0.0.0', help="The host IP to run the webserver on.")
parser.add_argument("--port", type=int, default=8080, help="The port to run the webserver on.")
parser.add_argument("--debug", type=bool, default=False, help="Whether or not to run the website in debug mode")
args = parser.parse_args()

# Read config
with open(args.config_file) as a:
    config = toml.load(a)

# Create website object - don't start based on argv
app = Application(loop=asyncio.get_event_loop(), debug=args.debug)
app['static_root_url'] = '/static'
app.router.add_routes(website.frontend_routes)
app.router.add_routes(website.backend_routes)
app.router.add_static('/static', os.getcwd() + '/website/static', append_version=True)

# Add middlewares
# if args.debug:
#     session_setup(app, SimpleCookieStorage(max_age=1_000_000))
# else:
session_setup(app, ECS(os.urandom(32), max_age=1_000_000))
jinja_setup(app, loader=FileSystemLoader(os.getcwd() + '/website/templates'))

# Add our connections and their loggers
app['database'] = utils.DatabaseConnection
utils.DatabaseConnection.logger = logger.getChild("db")
app['redis'] = utils.RedisConnection
utils.RedisConnection.logger = logger.getChild("redis")
app['logger'] = logger.getChild("route")

# Add our configs
app['config'] = config

# Add our bots
app['bot'] = utils.Bot(config_file=args.config_file, logger=logger.getChild("bot"))


if __name__ == '__main__':
    """Starts the bot (and webserver if specified) and runs forever"""

    loop = app.loop

    # Connect the bot
    logger.info("Logging in bot")
    loop.run_until_complete(app['bot'].login(app['config']['authorization_tokens']['bot']))

    # Connect the database
    if app['config'].get('database', {}).get('enabled', True):
        logger.info("Creating database pool")
        loop.run_until_complete(utils.DatabaseConnection.create_pool(app['config']['database']))

    # Connect the redis pool
    if app['config'].get('redis', {}).get('enabled', True):
        logger.info("Creating redis pool")
        loop.run_until_complete(utils.RedisConnection.create_pool(app['config']['redis']))

    # Start the server unless I said otherwise
    webserver = None

    # HTTP server
    logger.info("Creating webserver...")
    application = AppRunner(app)
    loop.run_until_complete(application.setup())
    webserver = TCPSite(application, host=args.host, port=args.port)

    # Start server
    loop.run_until_complete(webserver.start())
    logger.info(f"Server started - http://{args.host}:{args.port}/")

    # This is the forever loop
    try:
        logger.info("Running webserver")
        loop.run_forever()
    except KeyboardInterrupt:
        pass

    # Clean up our shit
    logger.info("Closing webserver")
    loop.run_until_complete(application.cleanup())
    if app['config'].get('database', {}).get('enabled', True):
        logger.info("Closing database pool")
        loop.run_until_complete(utils.DatabaseConnection.pool.close())
    if app['config'].get('redis', {}).get('enabled', True):
        logger.info("Closing redis pool")
        utils.RedisConnection.pool.close()
    logger.info("Closing bot")
    loop.run_until_complete(app['bot'].close())
    logger.info("Closing asyncio loop")
    loop.close()
