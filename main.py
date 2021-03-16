import riot_api,utils
import asyncio 
import json
import base64
import pypresence
import time
import threading
import pystray
from pystray import Icon as icon, Menu as menu, MenuItem as item
from PIL import Image, ImageDraw
import os
import subprocess
import psutil
import ctypes
import sys
import webserver
import oauth
import client_api
import match_session
from dotenv import load_dotenv
from psutil import AccessDenied
import nest_asyncio


nest_asyncio.apply()
global systray
load_dotenv()


systray = None
window_shown = False
client_id = str(os.environ.get('CLIENT_ID'))
#RPC = Presence(client_id)
client = None
launch_timeout = 120
last_presence = {}
session = None
last_state = None

#weird workaround for getting image to work with pyinstaller
def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'): 
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# ----------------------------------------------------------------------------------------------
# console/taskbar control stuff!
# thanks for some of this pete (github/restrafes) :)
kernel32 = ctypes.WinDLL('kernel32')
user32 = ctypes.WinDLL('user32')
hWnd = kernel32.GetConsoleWindow()

# prevent interaction of the console window which pauses execution
kernel32 = ctypes.windll.kernel32
kernel32.SetConsoleMode(kernel32.GetStdHandle(-10), 128)

# console visibility toggle functionality
def tray_window_toggle(icon, item):
    try:
        global window_shown
        window_shown = not item.checked
        if window_shown:
            user32.ShowWindow(hWnd, 1)
        else:
            user32.ShowWindow(hWnd, 0)
    except:
        pass

print("initializing systray object")
def run_systray():
    global systray, window_shown

    systray_image = Image.open(resource_path("favicon.ico"))
    systray_menu = menu(
        item('show debug', tray_window_toggle, checked=lambda item: window_shown),
        item('quit', close_program),
    )
    systray = pystray.Icon("valorant-rpc", systray_image, "valorant-rpc", systray_menu)
    systray.run()
print("systray ready!")

def close_program():
    global systray,client
    user32.ShowWindow(hWnd, 1)
    client.close()
    systray.stop()
    sys.exit()
#end sys tray stuff
# ----------------------------------------------------------------------------------------------



def update_rpc(state):

    global session

    data = json.loads(base64.b64decode(state))
    print(data)

    #party state
    party_state = "Solo" 
    if data["partySize"] > 1:
        party_state = "In a Party"
    party_state = "In an Open Party" if not data["partyAccessibility"] == "CLOSED" else party_state

    queue_id = utils.queue_ids[data["queueId"]]
    if data["partyState"] == "CUSTOM_GAME_SETUP":
        queue_id = "Custom"

    party_size = [data["partySize"],data["maxPartySize"]]

    #queue timing stuff
    time = utils.parse_time(data["queueEntryTime"])
    if not data["partyState"] == "MATCHMAKING" and not data["sessionLoopState"] == "INGAME" and not data["partyState"] == "MATCHMADE_GAME_STARTING" and not data["sessionLoopState"] == "PREGAME":
        time = False
    if data["partyState"] == "CUSTOM_GAME_SETUP":
        time = False

    join_state = f"partyId/{data['partyId']}" if data["partyAccessibility"] == "OPEN" else None

 
    if not data["isIdle"]:
        #menu
        if data["sessionLoopState"] == "MENUS" and data["partyState"] != "CUSTOM_GAME_SETUP":
            client.set_activity(
                state=party_state,
                details=("In Queue" if data["partyState"] == "MATCHMAKING" else "Lobby") + (f" - {queue_id}" if queue_id else ""),
                start=time if not time == False else None,
                large_image=("game_icon_white" if data["partyState"] == "MATCHMAKING" else "game_icon"),
                large_text="VALORANT",
                small_image="crown_icon" if utils.validate_party_size(data) else None,
                small_text="Party Leader" if utils.validate_party_size(data) else None,
                party_id=data["partyId"],
                party_size=party_size,
                join=join_state
            )

        #custom setup
        elif data["sessionLoopState"] == "MENUS" and data["partyState"] == "CUSTOM_GAME_SETUP":
            game_map = utils.maps[data["matchMap"].split("/")[-1]]
            client.set_activity(
                state=party_state,
                details="Lobby" + (f" - {queue_id}" if queue_id else ""),
                start=time if not time == False else None,
                large_image=f"splash_{game_map.lower()}_square",
                large_text=game_map,
                small_image="crown_icon" if utils.validate_party_size(data) else None,
                small_text="Party Leader" if utils.validate_party_size(data) else None,
                party_id=data["partyId"],
                party_size=party_size,
                join=join_state
            )

        elif data["sessionLoopState"] == "PREGAME":
            if last_state != "PREGAME":
                if session is None: 
                    session = match_session.Session(client)
                    asyncio.run(session.init_pregame(data))


    elif data["isIdle"]:
        client.set_activity(
            state="Away",
            details="Lobby" + (f" - {queue_id}" if queue_id else ""),
            large_image="game_icon",
            large_text="VALORANT",
            small_image="away_icon",
        )


def join_listener(data):
    config = utils.get_config()
    username = config['riot-account']['username']
    password = config['riot-account']['password']
    uuid,headers = asyncio.run(client_api.get_auth(username,password))
    party_id = data['secret'].split('/')[1]
    print(party_id)
    client_api.post_glz(f'/parties/v1/players/{uuid}/joinparty/{party_id}',headers)
    #somehow this works!


async def listen(lockfile):
    global last_presence,client,session
    while True:
        try:
            if not utils.is_process_running():
                print("valorant closed, exiting")
                close_program()

            #event listeners
            client.register_event('ACTIVITY_JOIN',join_listener)

            if session is None:
                presence = riot_api.get_presence(lockfile)
                if presence == last_presence:
                    last_presence = presence
                    continue
                update_rpc(presence)
                last_presence = presence
                await asyncio.sleep(1)
            else:
                # while in pregame update less often because less is changing and rate limits
                await session.loop()
                await asyncio.sleep(10)
        except:
            if not utils.is_process_running():
                print("valorant closed, exiting")
                close_program()



# ----------------------------------------------------------------------------------------------
# startup
async def main(loop):
    global client
    # setup client
    client = pypresence.Client(client_id,loop=loop) 
    webserver.run()
    client.start()
    oauth.authorize(client)
    
    launch_timer = 0

    #check if val is open
    if not utils.is_process_running():
        print("valorant not opened, attempting to run...")
        subprocess.Popen([os.environ['RCS_PATH'], "--launch-product=valorant", "--launch-patchline=live"])
        while not utils.is_process_running():
            print("waiting for valorant...")
            launch_timer += 1
            if launch_timer >= launch_timeout:
                close_program()
            time.sleep(1)

    #game launching, set loading presence
    client.set_activity(
        state="Loading",
        large_image="game_icon",
        large_text="valorant-rpc by @cm_an#2434"
    )

    #check for lockfile
    launch_timer = 0
    lockfile = riot_api.get_lockfile()
    if lockfile is None:
        while lockfile is None:
            print("waiting for lockfile...")
            lockfile = riot_api.get_lockfile()
            launch_timer += 1
            if launch_timer >= launch_timeout:
                close_program()
            time.sleep(1)
    print("lockfile loaded! hiding window in 3 seconds...")
    time.sleep(3)
    systray_thread = threading.Thread(target=run_systray)
    systray_thread.start()
    user32.ShowWindow(hWnd, 0)

    #check for presence
    launch_timer = 0
    presence = riot_api.get_presence(lockfile)
    if presence is None:
        while presence is None:
            print("waiting for presence...")
            presence = riot_api.get_presence(lockfile)
            launch_timer += 1
            if launch_timer >= launch_timeout:
                print("presence took too long, terminating program!")
                close_program()
            time.sleep(1)
    update_rpc(presence)
    print(f"LOCKFILE: {lockfile}")

    #start the loop
    await listen(lockfile)

if __name__=="__main__":   
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main(loop))
# ----------------------------------------------------------------------------------------------