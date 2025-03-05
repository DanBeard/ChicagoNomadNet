#!/usr/bin/env python3
print("#!c=3600") # cache for like an hr. Can also refresh if needed

import psutil
# seconds to in hh:mm:ss 
def convertTime(seconds): 
    if seconds == psutil.POWER_TIME_UNKNOWN:
      return "N/A"
    elif seconds == psutil.POWER_TIME_UNLIMITED:
      return "unlimited power!"

    minutes, seconds = divmod(seconds, 60) 
    hours, minutes = divmod(minutes, 60) 
    return "%dh %02dm" % (hours, minutes) 
  

bat = psutil.sensors_battery() 
bat_stats= f"{int(bat.percent)}% plugged in" if bat.power_plugged else f"{int(bat.percent)}% discharging. {convertTime(bat.secsleft)} left"
ram = psutil.virtual_memory()
page=f"""
               ╷ ╷
               │ │                   Chicago Nomad
               ║ ║                                     
               ║ ║                   Apps (Work in progress) :                           
              ▐███▌                    `F66d`[Offline zim of Wikipedia, Stackoverflow, & manuals`:/page/zr.mu]`f 
              ▐███▌      │   │         QR code router at lxmf@109bcc2a640466a04b533134ba0d071d
              ▐███▌      ╽   ╽              Send it an image LXMF QR code & it will send the message
             ▐█████▌     ┃   ┃
             ▐█████▌     █████         LoRa locations: 
       ▄██▄  ▐█████▌     █████           Grant Park 
       ████ ▐███████▌    █████                      
       ████ ▐███████▌   ▐█████▌          You nearby? let's mesh chicago up!
     ▐██████▐███████▌   ▐█████▌             Freq: 914.875 Mhz, Bandwidth: 125 Khz, SF: 8
     ▐██████▐████▐█████▌▐█████▌                  
     ▐██████▐████▐█████▌███████                         
  █████▌████▐████▐█████▌████▐████                     
  █████▌████▐████▐█████▌████▐████                      
 
`c
  I'm still getting the hang of this reticulum thing. If you have any advice, let me know lxmf@8c3b233ce031f821e930b07cb0b07f52

  Be sure to check back often to stay ... in the loop

`BFFF`F00F▄▄▄▄▄▄▄▄▄▄▄`f`b
`BFFF`FF00  ✶ ✶ ✶ ✶  `f`b
`BFFF`F00F▀▀▀▀▀▀▀▀▀▀▀`f`b
`c

>>>>Running on a downclocked system76 lem9 in the windy city
Battery  : {bat_stats}
Load     : {psutil.getloadavg()}
RAM      : {ram.percent}% used of {int(ram.total/1000/1000/1000)}GB

>>>>ascii art made by https://github.com/connordelacruz/chicago-ascii.sh
"""
print(page)
