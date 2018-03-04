#!/bin/bash
cd /home/steve/stephensbot/server
export FLASK_APP=server.py
tmux new-session -d -s stephensbot_server 'flask run'
