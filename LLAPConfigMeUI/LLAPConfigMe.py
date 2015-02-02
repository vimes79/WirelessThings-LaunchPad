#!/usr/bin/env python
# -*- coding: utf-8 -*-
""" LLAP ConfigMe
    Copyright (c) 2014 Ciseco Ltd.
    
    Author: Matt Lloyd
    
    This code is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
    
"""

import Tkinter as tk
import ttk
import sys
import os
import argparse
import socket
import select
import json
import ConfigParser
import tkMessageBox
import threading
import Queue
import string
import re
from time import sleep, asctime, time
import logging

"""
    Big TODO list
    
    DONE: JSON over UDP
    
    DONE: UDP open sockets
    
    DONE: JSON encode outgoing messages
    DONE: JSON decode incomming messages
    
    DONE: JSON debug window
    Pretty JSON formation for window?
    
    DONE: type: SERVER status check on start up
        basic PING
        
    DONE: use logger for debug output
    
    DONE: timeouts wait windows base on timeout's sent with LCR
    
    DONE: keepAwake via JSON's
    
    DONE: check replies for state, PASS, FAIL_RETRY, FAIL_TIMEOUT
    
    DONE: disable next button while waiting for query
    
    read AT settings for PANID and ENCRYPTION from server and offer as defuats to the user on a new device
    
    get JSON device file from network
    
    offer susgested settings based on json
    
    
"""


INTRO = """Welcome to LLAP Config me wizard
    
Please wait while we try to reach a LLAP Trasnfer service"""

INTRO1 = """Welcome to LLAP Config me wizard
    
A LLAP Transfer service has be found running on this network.
    
Please press the Config Me button on your device and click next"""

CONFIG = """Select your device config options"""

END = """Your device has been configured"""


class LLAPCongfigMeClient:
    """
        LLAP ConfigMe Client Class
        Handles display of wizard interface for configuring devices
        pass requests onto LLAPConfigMeCore
    """

    _version = 0.01
    
    _configFileDefault = "LLAPCM_defaults.cfg"
    _configFile = "LLAPCM.cfg"
    _myNodesFile = "MyNodes.json"
    
    _rows = 19
    _rowHeight = 28
    _widthMain = 604
    _heightMain = (_rows*_rowHeight)+4
    _widthSerial = 600
    _heightSerial = 200
    
    # how long to wait for a reply before asking user to press button again in seconds
    _timeout = 40
    _devIDInputs = []
    _encryptionKeyInput = 0
    _lastLCR = []
    _keepAwake = 0
    _currentFrame = None
    
    _validID = "ABCDEFGHIJKLMNOPQRSTUVWXYZ-#@?\\*"
    _validData = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 !\"#$%&'()*+,-.:;<=>?@[\\\/]^_`{|}~"

    def __init__(self):
        """
            setup variables
        """
        self._running = False

        logging.getLogger().setLevel(logging.NOTSET)
        self.logger = logging.getLogger('LLAPServer')
        self._ch = logging.StreamHandler()
        self._ch.setLevel(logging.DEBUG)    # this should be WARN by default
        self._formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self._ch.setFormatter(self._formatter)
        self.logger.addHandler(self._ch)
    
        # JSON Debug window Q
        self.qJSONDebug = Queue.Queue()
        # LCR Reply Q, Incomming JSON's from the server
        self.qLCRReply = Queue.Queue()
        # flag to show the server is alive
        self.fServerGood = threading.Event()

    def _initLogging(self):
        """ now we have the config file loaded and the command line args setup
            setup the loggers
            """
        self.logger.info("Setting up Loggers. Console output may stop here")
        
        # disable logging if no options are enabled
        if (self.args.debug == False and
            self.config.getboolean('Debug', 'console_debug') == False and
            self.config.getboolean('Debug', 'file_debug') == False):
            self.logger.debug("Disabling loggers")
            # disable debug output
            self.logger.setLevel(100)
            return
        # set console level
        if (self.args.debug or self.config.getboolean('Debug', 'console_debug')):
            self.logger.debug("Setting Console debug level")
            if (self.args.log):
                logLevel = self.args.log
            else:
                logLevel = self.config.get('Debug', 'console_level')
            
            numeric_level = getattr(logging, logLevel.upper(), None)
            if not isinstance(numeric_level, int):
                raise ValueError('Invalid console log level: %s' % loglevel)
            self._ch.setLevel(numeric_level)
        else:
            self._ch.setLevel(100)
        
        # add file logging if enabled
        # TODO: look at rotating log files
        # http://docs.python.org/2/library/logging.handlers.html#logging.handlers.TimedRotatingFileHandler
        if (self.config.getboolean('Debug', 'file_debug')):
            self.logger.debug("Setting file debugger")
            self._fh = logging.FileHandler(self.config.get('Debug', 'log_file'))
            self._fh.setFormatter(self._formatter)
            logLevel = self.config.get('Debug', 'file_level')
            numeric_level = getattr(logging, logLevel.upper(), None)
            if not isinstance(numeric_level, int):
                raise ValueError('Invalid console log level: %s' % loglevel)
            self._fh.setLevel(numeric_level)
            self.logger.addHandler(self._fh)
            self.logger.info("File Logging started")

    def on_excute(self):
        """
            entry point for running
        """
        self._checkArgs()
        self._readConfig()
        self._initLogging()
        self._loadDevices()
        
        self._running = True

        # run the GUI's
        self._runConfigMe()
        self._cleanUp()

    def _runConfigMe(self):
        self.logger.debug("Running Main GUI")
        self.master = tk.Tk()
        self.master.protocol("WM_DELETE_WINDOW", self._endConfigMe)
        self.master.geometry(
                 "{}x{}+{}+{}".format(self._widthMain,
                                      self._heightMain,
                                      self.config.get('LLAPCM',
                                                      'window_width_offset'),
                                      self.config.get('LLAPCM',
                                                      'window_height_offset')
                                      )
                             )

        self.master.title("LLAP Config Me v{}".format(self._version))
        self.master.resizable(0,0)
        
        self._initTkVariables()
        self._initValidationRules()
        
        if self.args.debug or self.config.getboolean('Debug', 'gui_json'):
            self._jsonWindowDebug()
        
        self._initUDPListenThread()
        self._initUDPSendThread()
        
        # TODO: are UDP threads running
        if (not self.tUDPListen.isAlive() and not self.tUDPSend.isAlive()):
            self.logger.warn("UDP Threads not running")
            # TODO: do we have an error form the UDP to show?
        else:
            # dispatch a server status request
            self.qUDPSend.put(json.dumps({"type": "Server"}))
            
            self._displayIntro()
            
            self.master.mainloop()
    
    def _initUDPSendThread(self):
        """ Start the UDP output thread
            """
        self.logger.info("UDP Send Thread init")
        
        self.qUDPSend = Queue.Queue()
        
        self.tUDPSendStop = threading.Event()
        
        self.tUDPSend = threading.Thread(target=self._UDPSendTread)
        self.tUDPSend.daemon = False
        
        try:
            self.tUDPSend.start()
        except:
            self.logger.exception("Failed to Start the UDP send thread")

    def _UDPSendTread(self):
        """ UDP Send thread
        """
        self.logger.info("tUDPSend: Send thread started")
        # setup the UDP send socket
        try:
            UDPSendSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except socket.error, msg:
            self.logger.critical("tUDPSend: Failed to create socket. Error code : {} Message : {}".format(msg[0], msg[1]))
            # TODO: tUDPSend needs to stop here
            # TODO: need to send message to user saying could not open socket
            self.die()
            return
        
        UDPSendSocket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        UDPSendSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        sendPort = int(self.config.get('UDP', 'send_port'))
        
        while not self.tUDPSendStop.is_set():
            try:
                message = self.qUDPSend.get(timeout=1)     # block for up to 30 seconds
            except Queue.Empty:
                # UDP Send que was empty
                # extrem debug message
                # self.logger.debug("tUDPSend: queue is empty")
                pass
            else:
                self.logger.debug("tUDPSend: Got json to send: {}".format(message))
                try:
                    UDPSendSocket.sendto(message, ('<broadcast>', sendPort))
                    self.logger.debug("tUDPSend: Put message out via UDP")
                except socket.error, msg:
                    self.logger.warn("tUDPSend: Failed to send via UDP. Error code : {} Message: {}".format(msg[0], msg[1]))
                else:
                    self.qJSONDebug.put([message, "TX"])
                # tidy up

                self.qUDPSend.task_done()

            # TODO: tUDPSend thread is alive, wiggle a pin?

        self.logger.info("tUDPSend: Thread stopping")
        try:
            UDPSendSocket.close()
        except socket.error:
            self.logger.exception("tUDPSend: Failed to close socket")
        return

    def _initUDPListenThread(self):
        """ Start the UDP Listen thread and queues
        """
        self.logger.info("UDP Listen Thread init")

        self.tUDPListenStop = threading.Event()
        
        self.tUDPListen = threading.Thread(target=self._UDPListenThread)
        self.tUDPListen.deamon = False

        try:
            self.tUDPListen.start()
        except:
            self.logger.exception("Failed to Start the UDP listen thread")

    def _UDPListenThread(self):
        """ UDP Listen Thread
        """
        self.logger.info("tUDPListen: UDP listen thread started")
        
        try:
            UDPListenSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except socket.error:
            self.logger.exception("tUDPListen: Failed to create socket, stopping")
            # TODO: need to send message to user saying could not open socket
            self.die()
            return

        UDPListenSocket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        UDPListenSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if sys.platform == 'darwin':
            UDPListenSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        
        try:
            UDPListenSocket.bind(('', int(self.config.get('UDP', 'listen_port'))))
        except socket.error:
            self.logger.exception("tUDPListen: Failed to bind port")
            self.die()
            return
        UDPListenSocket.setblocking(0)
        
        self.logger.info("tUDPListen: listening")
        while not self.tUDPListenStop.is_set():
            ready = select.select([UDPListenSocket], [], [], 3)  # 3 second time out using select
            if ready[0]:
                (data, address) = UDPListenSocket.recvfrom(2048)
                self.logger.debug("tUDPListen: Received JSON: {} From: {}".format(data, address))
                jsonin = json.loads(data)
                self.qJSONDebug.put([data, "RX"])
                if jsonin['type'] == "LLAP":
                    self.logger.debug("tUDPListen: JSON of type LLAP")
                    # got a LLAP type json, need to generate the LLAP message and
                    # TODO: we should pass on LLAP type to the JSON window if enabled
                    
                elif jsonin['type'] == "LCR":
                    # we have a LLAPConfigRequest reply pass it back to the GUI to deal with
                    self.logger.debug("tUDPListen: JSON of type LCR, passing to qLCRReply")
                    try:
                        self.qLCRReply.put_nowait(jsonin)
                    except Queue.Full:
                        self.logger.debug("tUDPListen: Failed to put json on qLCRReply")

                elif jsonin['type'] == "Server":
                    # TODO: we have a SERVER json do stuff with it
                    self.logger.debug("tUDPListen: JSON of type SERVER")
                    if jsonin['state'] == "RUNNING":
                        self.fServerGood.set()

        self.logger.info("tUDPListen: Thread stopping")
        try:
            UDPListenSocket.close()
        except socket.error:
            self.logger.exception("tUDPListen: Failed to close socket")
        return
    
    def _initTkVariables(self):
        self.logger.debug("Init Tk Variables")
        # any tk varaibles we need to keep permentant
        
        # init the entry variables we will need to reset between each run
        self._initEntryVariables()
        
    def _initEntryVariables(self):
        self.logger.debug("Init entry Variables")
        # format for each entry is as follows
        # 'commmand': [current Value, old Value, type Off Output]
        #
        # type of Output
        # this is based on the format field from the json
        # with the execption of ENKEY
        # type is used in conjuction with how these fields are dispayed for user
        # input and how we procees that for LCR output
        # most are just stright copy outputs but some like ONOF and ENKEY require speical handeling
        self.entry = {
                      "CHDEVID" : [tk.StringVar(), tk.StringVar(), 'ID'],
                      "PANID" : [tk.StringVar(), tk.StringVar(), 'ID'],
                      "RETRIES" : [tk.StringVar(), tk.StringVar(), 'Int'],
                      "INTVL" : [tk.StringVar(), tk.StringVar(), 'Period'],
                      "WAKEC" : [tk.StringVar(), tk.StringVar(), 'Int'],
                      "SLEEPM" : [tk.IntVar(), tk.IntVar(), 'SleepMode'],
                      "SNL" : [tk.StringVar(), tk.StringVar(), 'ReadOnlyHex'],
                      "SNH" : [tk.StringVar(), tk.StringVar(), 'ReadOnlyHex'],
                      "ENC" : [tk.IntVar(), tk.IntVar(), 'ONOFF'],
                      "ENKEY" : [tk.StringVar(), tk.StringVar(), 'ENKey']
                     }

    def _displayIntro(self):
        self.logger.debug("Display Intro Page")
        self.iframe = tk.Frame(self.master, name='introFrame', relief=tk.RAISED,
                               borderwidth=2, width=self._widthMain,
                               height=self._heightMain)
        self.iframe.pack()
        self._currentFrame = 'introFrame'
        
        self._buildGrid(self.iframe)
        
        tk.Label(self.iframe, name='introText', text=INTRO
                 ).grid(row=1, column=0, columnspan=6, rowspan=self._rows-4)

        tk.Button(self.iframe, text='Back', state=tk.DISABLED
                  ).grid(row=self._rows-2, column=4, sticky=tk.E)
        tk.Button(self.iframe, name='next', text='Next', command=self._queryType,
                  state=tk.DISABLED
                  ).grid(row=self._rows-2, column=5, sticky=tk.W)
        self._checkServerCount = 0
        self.master.after(1000, self._checkServerUpdate)
    
    def _checkServerUpdate(self):
        self.logger.debug("Checking server reply flag")
        if self.fServerGood.is_set():
            #we have a good server update Intro page
            self.logger.debug("Server found ok")
            self.iframe.children['introText'].config(text=INTRO1)
            self.iframe.children['next'].config(state=tk.ACTIVE)
            return
        elif self._checkServerCount == 5:
            # half of time out send request again
            self.qUDPSend.put(json.dumps({"type": "Server"}))
        elif self._checkServerCount == 10:
            # timeout (should be about 30 seconds
            # cant find a server display pop up and quit?
            if tkMessageBox.askyesno("Server Timeout",
                                     ("Unable to get a response from a LLAPServer \n"
                                      "Click Yes to try again \n"
                                      "Click No to Quit")
                                     ):
                # try again
                self.qUDPSend.put(json.dumps({"type": "Server"}))
                self._checkServerCount = 0
            else:
                self._endConfigMe()
                return
        self._checkServerCount += 1
        self.master.after(1000, self._checkServerUpdate)
        
    def _displayConfig(self):
        self.logger.debug("Displaying Device type based config screen")
        self.master.children[self._currentFrame].pack_forget()
                
        self.cframe = tk.Frame(self.master, name='configFrame', relief=tk.RAISED,
                               borderwidth=2, width=self._widthMain,
                               height=self._heightMain)
        self.cframe.pack()
        self._currentFrame = 'configFrame'

        self._buildGrid(self.cframe)

        tk.Label(self.cframe, text=CONFIG).grid(row=0, column=0, columnspan=6)
        
        # generic config options
        tk.Label(self.cframe, text="Generic Commands"
                 ).grid(row=1, column=0, columnspan=3)
                 
        tk.Label(self.cframe, text="Device ID").grid(row=2, column=0, columnspan=3)
        tk.Label(self.cframe, text="CHDEVID").grid(row=3, column=0, sticky=tk.E)
        self._devIDInputs.append(tk.Entry(self.cframe,
                                          textvariable=self.entry['CHDEVID'][0],
                                          width=20,
                                          validate='key',
                                          invalidcommand='bell',
                                          validatecommand=self.vDevID,
                                          name='chdevid'
                                         )
                                )
        self._devIDInputs[-1].grid(row=3, column=1, columnspan=2, sticky=tk.W)
                 
        tk.Label(self.cframe, text="Pan ID").grid(row=4, column=0, columnspan=3)
        tk.Label(self.cframe, text="PANID").grid(row=5, column=0, sticky=tk.E)
        tk.Entry(self.cframe, textvariable=self.entry['PANID'][0], width=20,
                 validate='key',
                 invalidcommand='bell',
                 validatecommand=self.vUpper,
                 ).grid(row=5, column=1, columnspan=2, sticky=tk.W)
         
        tk.Label(self.cframe, text="Retries for Announcements"
                 ).grid(row=6, column=0, columnspan=3)
        tk.Label(self.cframe, text="RETRIES").grid(row=7, column=0, sticky=tk.E)
        tk.Entry(self.cframe, textvariable=self.entry['RETRIES'][0], width=20
                 ).grid(row=7, column=1, columnspan=2, sticky=tk.W)
        
        if self.devices[self.device['id']]['SleepMode'] == "Cyclic":
            # cyclic config options
            tk.Label(self.cframe, text="Cyclic Commands"
                     ).grid(row=9, column=0, columnspan=3)
            tk.Label(self.cframe, text="Sleep Interval"
                     ).grid(row=10, column=0, columnspan=3)
            tk.Label(self.cframe, text="INTVL").grid(row=11, column=0, sticky=tk.E)
            tk.Entry(self.cframe, textvariable=self.entry['INTVL'][0], width=20,
                     validate='key',
                     invalidcommand='bell',
                     validatecommand=self.vUpper,
                    ).grid(row=11, column=1, columnspan=2, sticky=tk.W)
    
            tk.Label(self.cframe, text="Battery Wake Count"
                     ).grid(row=12, column=0, columnspan=3)
            tk.Label(self.cframe, text="WAKEC").grid(row=13, column=0, sticky=tk.E)
            tk.Entry(self.cframe, textvariable=self.entry['WAKEC'][0], width=20,
                    ).grid(row=13, column=1, columnspan=2, sticky=tk.W)
        
            tk.Label(self.cframe, text="Enable Cyclic Sleep"
                     ).grid(row=14, column=0, columnspan=3)
            tk.Label(self.cframe, text="CYCLE").grid(row=15, column=0, sticky=tk.E)
            tk.Checkbutton(self.cframe, variable=self.entry['SLEEPM'][0]
                          ).grid(row=15, column=1, columnspan=2, sticky=tk.W)
        elif self.devices[self.device['id']]['SleepMode'] == "Cyclic":
            # Interrupt sleep devices
            tk.Label(self.cframe, text="Interrupt Sleep"
                     ).grid(row=9, column=0, columnspan=3)
            tk.Label(self.cframe, text="SLEEP").grid(row=10, column=0, sticky=tk.E)
            tk.Checkbutton(self.cframe, variable=self.entry['SLEEPM'][0]
                          ).grid(row=10, column=1, columnspan=2, sticky=tk.W)
        
        # device config options
        tk.Label(self.cframe,
                 text="{} Options".format(self.devices[self.device['id']]['Name'])
                 ).grid(row=1, column=3, columnspan=3)
        r = 0
        for n in self.devices[self.device['id']]['Options']:
            
            tk.Label(self.cframe, text=n['Description']
                     ).grid(row=2+r, column=3, columnspan=3)
            tk.Label(self.cframe, text=n['Command']
                     ).grid(row=3+r, column=3, sticky=tk.E)
            if n['Format'] == "ONOFF":
                e = tk.Checkbutton(self.cframe, variable=self.entry[n['Command']][0],
                                   onvalue="ON", offvalue="OFF",
                                   name=n['Command'].lower()
                                   )
                e.grid(row=3+r, column=4, columnspan=2, sticky=tk.W)
            else:
                e = tk.Entry(self.cframe, textvariable=self.entry[n['Command']][0],
                             name=n['Command'].lower()
                             )
                e.grid(row=3+r, column=4, columnspan=2, sticky=tk.W)
                if n['Format'] == "Int":
                    e.config(validate='key',
                             invalidcommand='bell',
                             validatecommand=self.vInt)
                elif n['Format'] == "String":
                    e.config(validate='key',
                             invalidcommand='bell',
                             validatecommand=self.vUpper)
                elif n['Format'] == "ID":
                    e.config(validate='key',
                             invalidcommand='bell',
                             validatecommand=self.vDevID)
                    self._devIDInputs.append(e)
                    
            r += 2
        
        # buttons
        tk.Button(self.cframe, text='Advanced', command=self._displayAdvance
                  ).grid(row=self._rows-2, column=2, columnspan=2,
                         sticky=tk.E+tk.W)
        tk.Button(self.cframe, text='Back', state=tk.DISABLED
                  ).grid(row=self._rows-2, column=4, sticky=tk.E)
        tk.Button(self.cframe, name='next', text='Next', command=self._sendConfigRequest
                  ).grid(row=self._rows-2, column=5, sticky=tk.W)
    
    def _entryCopy(self):
        for key, value in self.entry.items():
            value[1].set(value[0].get())

    def _displayAdvance(self):
        """Advance config diag to show Serial number and set ENC"""
        # TODO: rearrange to fit long ENKEY box
        # TODO: should we also get FVER and display that?
        self.logger.debug("Display advance config screen")
    
        position = self.master.geometry().split("+")
            
        self.advanceWindow = tk.Toplevel()
        self.advanceWindow.geometry("+{}+{}".format(
                                                     int(position[1])+self._widthMain/6,
                                                     int(position[2])+self._heightMain/6
                                                     )
                                     )
                                     
        self.advanceWindow.title("Advance config")
    
        self.aframe = tk.Frame(self.advanceWindow, name='advanceFrame', relief=tk.RAISED,
                               borderwidth=2, width=self._widthMain/6,
                               height=self._heightMain/6)
        self.aframe.pack()
        
        self._buildGrid(self.aframe, False, True)

        tk.Label(self.aframe, text="Advance configuration options"
                 ).grid(row=0, column=0, columnspan=6)
        
        tk.Label(self.aframe, text="Serial Number (read only)"
                 ).grid(row=1, column=0, columnspan=3)
    
        tk.Label(self.aframe, text="High Bytes").grid(row=2, column=0, columnspan=3)
        tk.Label(self.aframe, text="SNH").grid(row=3, column=0, sticky=tk.E)
        tk.Entry(self.aframe, textvariable=self.entry['SNH'][0], width=20,
                 state=tk.DISABLED
                 ).grid(row=3, column=1, columnspan=2, sticky=tk.W)
    
        tk.Label(self.aframe, text="Low Bytes").grid(row=4, column=0, columnspan=3)
        tk.Label(self.aframe, text="SNL").grid(row=5, column=0, sticky=tk.E)
        tk.Entry(self.aframe, textvariable=self.entry['SNL'][0], width=20,
                 state=tk.DISABLED
                 ).grid(row=5, column=1, columnspan=2, sticky=tk.W)
                 
        tk.Label(self.aframe, text="Encryption Options"
                 ).grid(row=1, column=3, columnspan=3)
    
        tk.Label(self.aframe, text="Enable Encryption"
                 ).grid(row=2, column=3, columnspan=3)
        tk.Label(self.aframe, text="ENC").grid(row=3, column=3, sticky=tk.E)
        tk.Checkbutton(self.aframe, variable=self.entry['ENC'][0]
                       ).grid(row=3, column=4, columnspan=2, sticky=tk.W)
    
        tk.Label(self.aframe, text="Encryption Key (set Only)"
                 ).grid(row=4, column=3, columnspan=3)
        tk.Label(self.aframe, text="EN[1-6]").grid(row=5, column=3, sticky=tk.E)
        self._encryptionKeyInput = tk.Entry(self.aframe,
                                            textvariable=self.entry['ENKEY'][0],
                                            width=33,
                                            validate='key',
                                            invalidcommand='bell',
                                            validatecommand=self.vEnKey,
                                            name='enkey')
                                            
        self._encryptionKeyInput.grid(row=5, column=4, columnspan=2, sticky=tk.W)


        tk.Button(self.aframe, text="Done", command=self._checkAdvance
                  ).grid(row=7, column=2, columnspan=2)
    
    def _displayEnd(self):
        self.logger.debug("Displaying end screen")
    
        self.master.children[self._currentFrame].pack_forget()

        self.eframe = tk.Frame(self.master, name='endFrame', relief=tk.RAISED,
                               borderwidth=2, width=self._widthMain,
                               height=self._heightMain)
        self.eframe.pack()
        self._currentFrame = 'endFrame'
        
        self._buildGrid(self.eframe)
        
        tk.Label(self.eframe, text=END).grid(row=1, column=0, columnspan=6,
                                              rowspan=self._rows-4)
                                              
        tk.Button(self.eframe, text='Back', state=tk.DISABLED
                ).grid(row=self._rows-2, column=4, sticky=tk.E)
        tk.Button(self.eframe, text='Start Over', command=self._startOver
                  ).grid(row=self._rows-2, column=5, sticky=tk.W)

    # validation rules

    # valid percent substitutions (from the Tk entry man page)
    # %d = Type of action (1=insert, 0=delete, -1 for others)
    # %i = index of char string to be inserted/deleted, or -1
    # %P = value of the entry if the edit is allowed
    # %s = value of entry prior to editing
    # %S = the text string being inserted or deleted, if any
    # %v = the type of validation that is currently set
    # %V = the type of validation that triggered the callback
    #      (key, focusin, focusout, forced)
    # %W = the tk name of the widget

    def _initValidationRules(self):
        self.logger.debug("Setting up GUI validation Rules")
        self.vUpper = (self.master.register(self.validUpper), '%d', '%P', '%S')
        self.vDevID = (self.master.register(self.validDevID), '%d',
                       '%P', '%W', '%P', '%S')
        self.vInt = (self.master.register(self.validInt), '%d', '%s', '%S')
        self.vHex = (self.master.register(self.validHex), '%d', '%s', '%S')
        self.vEnKey = (self.master.register(self.validEncryptionKey), '%d',
                       '%P', '%W', '%P', '%S')
    
    def validUpper(self, d, P, S):
        if S.islower():
            return False
        return True
            
    def validInt(self, d, s, S):
        if d == '0':
            return True
        if S.isdigit():
            return True
        else:
            return False

    def validHex(self, d, s, S):
        try:
            int(S, 16)          # is is a valid hex char
            return True
        except ValueError:
            return False
        return False
    
    def validEncryptionKey(self, d, P, W, s, S):
        valid = False
        if d == '0' or d == '-1':
            return True
        try:
            int(S, 16)          # is is a valid hex char
            valid = True
        except ValueError:
            return False
        
        if S.islower() and (len(P) <= 32):  # we already know is a HEX digit
            self.entry[W.split('.')[-1].upper()][0].set(P.upper())
            self.master.after_idle(self.vEnKeySet)
        elif valid and (len(P) <= 32):
            return True
        else:
            return False
    
    def validDevID(self, d, P, W, s, S):
        valid = False
        validChar = ['#', '@', '\\', '*'] # as of llap 2.0 - and ? cannot be set
        for c in validChar:
            if S.startswith(c):
                valid = True
        
        if d == '0' or d == '-1':
            return True
        elif S.islower() and (len(P) <= 2):
            self.entry[W.split('.')[-1].upper()][0].set(P.upper())
            self.master.after_idle(self.vdevSet)
        elif (S.isupper() or valid) and (len(P) <= 2):
            return True
        else:
            return False
    
    def vdevSet(self):
        for e in self._devIDInputs:
            e.icursor(e.index(tk.INSERT)+1)
            e.config(validate='key')

    def vEnKeySet(self):
        self._encryptionKeyInput.icursor(self._encryptionKeyInput.index(tk.INSERT)+1)
        self._encryptionKeyInput.config(validate='key')

    def _startOver(self):
        self.logger.debug("Starting over")
        self.master.children[self._currentFrame].pack_forget()
        self.iframe.pack()
        self._currentFrame = 'introFrame'
        # clear out entry variables
        self._initEntryVariables

    def _displayProgress(self):
        self.logger.debug("Displaying progress pop up")
        
        # disable current Next Button
        self.master.children[self._currentFrame].children['next'].config(state=tk.DISABLED)
        
        position = self.master.geometry().split("+")
        
        self.progressWindow = tk.Toplevel()
        self.progressWindow.geometry("+{}+{}".format(
                                             int(position[1])+self._widthMain/4,
                                             int(position[2])+self._heightMain/4
                                                     )
                                     )
            
        self.progressWindow.title("Working")

        tk.Label(self.progressWindow,
                 text="Communicating with device please wait").pack()

        self.progressBar = ttk.Progressbar(self.progressWindow,
                                           orient="horizontal", length=200,
                                           mode="indeterminate")
        self.progressBar.pack()
        self.progressBar.start()
    
    def _checkAdvance(self):
        self.logger.debug("Checking advance input")
        if len(self.entry["ENKEY"][0].get()) == 32 or len(self.entry["ENKEY"][0].get()) == 0:
            print self.entry["ENC"][0].get()
            self.advanceWindow.destroy()
            print self.entry["ENC"][0].get()
        else:
            # let user know KEY needs to be 0 or 32
            tkMessageBox.showerror("Encryption Key Length",
                                   ("Encryption key needs to be 32 characters"
                                    "long to set a new one or empty to leave unchanged"))
    
    def _sendConfigRequest(self):
        self.logger.debug("Sending config request to device")
        # TODO: add a line here to disable NEXT button on cfame and advance
        query = []
        for command, value in self.entry.items():
            print("Checking {}: {} != {}".format(command, value[0].get(), value[1].get()))
            if not value[0].get() == value[1].get():
                query = self._entryAppend(query, command, value)
                print query
        
        query.append({'command': "REBOOT"}) # we always send at least a reboot

        self._keepAwake = 0

        lcr = {"type": "LCR",
                "network":self.device['network'],
                "data":{
                    "id": 3,
                    "timeout": 60,
                    "keepAwake":self._keepAwake,
                    "devType": self.device['DTY'],
                    "toQuery": query
                    }
                    }
        self._lastLCR.append(lcr)
        self._sendRequest(lcr)

    def _entryAppend(self, query, command, value):
        """
            The following are use to append the correct LLAP commands
            to the passed query and return the altered query
            based on the type of Entery
            
        """
        if value[2] == 'String':
            query.append(
                         {'command': command,
                          'value': value[0].get()
                         }
                         )
        elif value[2] == 'Float':
            query.append(
                         {'command': command,
                         'value': value[0].get()
                         }
                         )
        elif value[2] == 'Int':
            query.append(
                         {'command': command,
                         'value': value[0].get()
                         }
                         )
        elif value[2] == 'ONOFF':
            if value[0].get() == 1:
                query.append({'command': command, 'value': "ON"})
            else:
                query.append({'command': command, 'value': "OFF"})

        elif value[2] == 'ONOFFTOG':
            query.append(
                         {'command': command,
                         'value': value[0].get()
                         }
                         )
        elif value[2] == 'ID':
            query.append(
                         {'command': command,
                         'value': value[0].get()
                         }
                         )
        elif value[2] == 'Hex':
            query.append(
                         {'command': command,
                         'value': value[0].get()
                         }
                         )
        elif value[2] == 'ReadOnlyHex':
            pass
        elif value[2] == 'Period':
            query.append(
                         {'command': command,
                         'value': value[0].get()
                         }
                         )
        elif value[2] == 'SleepMode':
            if self.devices[self.device['id']]['SleepMode'] == "Cyclic":
                query.append({'command': "SLEEPM",
                             'value': ("16" if self.entry['SLEEPM'][0].get() else "0")
                             }
                             )
            elif self.devices[self.device['id']]['SleepMode'] == "Interrupt":
                query.append({'command': "SLEEPM",
                             'value': ("8" if self.entry['SLEEPM'][0].get() else "0")
                             }
                             )
        elif value[2] == 'ENKey':
            # set encryption key
            # need to split into each EN[1-6]
            # Test keys
            #      ><    ><    ><    ><    ><>
            # 12345678901234567890123456789012
            # A1B2C3D4E5F6A2B3C4DE6F7A3B4C5D6E
            #self.logger.debug("ENKEY Length: {}".format(len(self.entry["ENKEY"][0].get())))
            if len(value[0].get()) == 32:
                # key is long enough
                query.append({'command': "EN1", 'value': value[0].get()[0:6]})
                query.append({'command': "EN2", 'value': value[0].get()[6:12]})
                query.append({'command': "EN3", 'value': value[0].get()[12:18]})
                query.append({'command': "EN4", 'value': value[0].get()[18:24]})
                query.append({'command': "EN5", 'value': value[0].get()[24:30]})
                query.append({'command': "EN6", 'value': value[0].get()[30:32]})
                self.entry[command][0].set("") # clear encryption key box

        return query

    def _queryType(self):
        """ Time to send a query to see if we have a device in pair mode
            this is going to need time out's? possible retries
            devtype and apver request
        """
        self.logger.debug("Query type")
        # TODO: add a line here to disable NEXT button on pfame
        query = [
                 {'command': "DTY"},
                 {'command': "APVER"},
                 {'command': "CHDEVID"}
                ]
        lcr = {"type": "LCR",
               "network":"ALL",
               "data":{
                       "id": 1,
                       "timeout": 30,   # short time out
                       "toQuery": query
                       }
              }
        
        self._lastLCR.append(lcr)
        self._sendRequest(lcr)
    
    def _processReply(self):
        self.logger.debug("Processing reply")
        # TODO: UDP get reply
        json = self.qLCRReply.get()
        reply = json['data']
        self.logger.debug("id: {}, devType:{}, Replies:{}".format(reply['id'],
                                                                reply.get('devType', ""),
                                                                reply['replies']))
        
        # check if reply is valid
        if reply['state'] == "FAIL_TIMEOUT":
            # TODO: handle failed due to timeout
            self.logger.debug("LCR timeout")
            # display pop up ask user to check configme mode and try again
            if tkMessageBox.askyesno("Comunications Timeout",
                                     ("Please check the deivce is in CONFIGME mode and \n"
                                      "Click yes to retry\n"
                                      "No to return to pervious screen")
                                     ):
                # send query again
                self._sendRequest(self._lastLCR[-1])
            else:
                pass
    
        elif reply['state'] == "FAIL_RETRY":
            # TODO: handle failed due to retry
            self.logger.debug("LCR retry error")
            # display pop up ask user to check configme mode and try again
            if tkMessageBox.askyesno("Comunications Timeout",
                                     ("Please check the deivce is in CONFIGME mode and \n"
                                      "Click yes to retry\n"
                                      "No to return to pervious screen")
                                     ):
                # send query again
                self._sendRequest(self._lastLCR[-1])
            else:
                pass
        elif reply['state'] == "PASS":
            # got a good reply
            self.logger.debug("got a good reply")
            
            # check reply ID with Expected ID
            if reply['id'] != self._lastLCR[-1]['data']['id']:
                # added this to cope with receiving multiple replies
                # e.g. if there are multiple network interfaces active
                self.master.after(500, self._replyCheck)
            else:
                self.logger.debug("reply is expected ID")
                
                # process reply
                if reply['id'] == 1:
                    # this was a query type request
                    if float(reply['replies']['APVER']['reply']) >= 2.0:
                        # valid apver
                        # so check what replied
                        for n in range(len(self.devices)):
                            if self.devices[n]['DTY'] == reply['replies']['DTY']['reply']:
                                # we have a match
                                self.logger.debug("Matched device")
                                self.device = {'id': n,
                                               'DTY': self.devices[n]['DTY'],   # copy form JSON not reply
                                               'devID': reply['replies']['CHDEVID']['reply'],
                                               'network': json['network']
                                              }
                                
                                # ask user about reseting device if devID is not ??
                                # for testing lets just reset if devID is MB
                                if self.device['devID'] != "??":
                                    if tkMessageBox.askyesno("Device Previously configured",
                                                             ("This device has been previously configured, \n"
                                                              "Do you wish to reset the device to defaults (Yes),\n"
                                                              "Or to alter the current configuration (No)")
                                                             ):
                                        query = [
                                                 {'command': "LLAPRESET"},
                                                 {'command': "CHDEVID"}
                                                ]
                                                
                                        self.logger.debug("Setting keepAwake")
                                        self._keepAwake = 1
                                        
                                        lcr = {"type": "LCR",
                                               "network":self.device['network'],
                                               "data":{
                                                       "id": 5,
                                                       "timeout": 60,
                                                       "keepAwake":self._keepAwake,
                                                       "devType": self.device['DTY'],
                                                       "toQuery": query
                                                      }
                                              }
                                                
                                        self._lastLCR.append(lcr)
                                        self._sendRequest(lcr)
                                    else:
                                        self._askCurrentConfig()
                                else:
                                    self._askCurrentConfig()
                                
                    else:
                        # apver mismatch, show error screen
                        pass
                elif reply['id'] == 2:
                    self.logger.debug("reply id is 2")
                    # this was an information request
                    # populate fields
                    if self.device['devID'] == '':
                        self.entry['CHDEVID'][0].set("--")
                    else:
                        self.entry['CHDEVID'][0].set(self.device['devID'])
                        
                    for command, args in reply['replies'].items():
                        if command == "CHREMID" and args['reply'] == '':
                            self.entry[command][0].set("--")
                        elif command == "SLEEPM":
                            value = int(args['reply'])
                            if value != 0:
                                self.entry[command][0].set(1)
                            else:
                                self.entry[command][0].set(0)
                        elif command == "ENC":
                            if args['reply'] == "OFF":
                                self.entry[command][0].set(0)
                            elif args['reply'] == "ON":
                                self.entry[command][0].set(1)
                            else:
                                #should not get here
                                self.logger.debug("Error in reply to ENC")
                        else:
                            if command in self.entry:
                                # TODO: need to handle check box entry (Format: ONOFF
                                self.entry[command][0].set(args['reply'])

                    # copy config so we can compare it later
                    self._entryCopy()
                    # show config screen
                    self.logger.debug("Setting keepAwake, display config")
                    # TODO: set keepAwake via UDP LCR
                    self._keepAwake = 1
                    self._displayConfig()

                elif reply['id'] == 3:
                    # this was a config request
                    # TODO: check replies were good and let user know device is now ready
                    enkeyCount = 0
                    enkeyMatch = 0
                    en = re.compile('^EN[1-6]')

                    for command, arg in reply['replies'].items():
                        if en.match(command):
                            enkeyCount += 1
                            if arg['reply'] == "ENACK":
                                enkeyMatch += 1
                        elif arg['value'] != arg['reply']:
                            # values don't match we should warn user
                            tkMessageBox.showerror("Value mismatch",
                                                   "The {} value was not set, \n Sent: {}\n Got back: {}".format(command, arg['value'], arg['reply']))

                    if enkeyCount != 0 and enkeyMatch != 6:
                        # encryption key not fully set
                        tkMessageBox.showerror("Encryption Key Error",
                                               "Your encryption key was not correctly set please try again")


                    # show end screen
                    self._displayEnd()
                elif reply['id'] == 4:
                    pass
                elif reply['id'] == 5:
                    # have done a reset so should get back factory settings
                    # check devi id is now ?? and update local
                    self.device['devID'] = reply['replies']['CHDEVID']['reply']
                    if self.device['devID'] == "??":
                        self._askCurrentConfig()
                    else:
                        # TODO: LLAPRESET didnt work ERROR
                        pass
        # TODO: clean up
        self.qLCRReply.task_done()

    def _askCurrentConfig(self):
        # assuming we know what it is ask for the current config
        self.logger.debug("Ask current config")
        query = [
                 {'command': "PANID"},
                 {'command': "RETRIES"},
                 {'command': "SNL"},
                 {'command': "SNH"},
                 {'command': "ENC"}
                 ]
        
        if self.devices[self.device['id']]['SleepMode'] == "Cyclic":
            query.append({'command': "INTVL"})
            query.append({'command': "WAKEC"})
            query.append({'command': "SLEEPM"})
        elif self.devices[self.device['id']]['SleepMode'] == "Interrupt":
            query.append({'command': "SLEEPM"})
        
        for n in self.devices[self.device['id']]['Options']:
            # create place to put the reply later
            self.entry[n['Command']] = [tk.StringVar(), tk.StringVar(), 'String']
            query.append({'command': n['Command'].encode('ascii', 'ignore')})
        
        
        self.logger.debug("Setting keepAwake")
        self._keepAwake = 1
        
        lcr = {"type": "LCR",
                "network":self.device['network'],
                "data":{
                    "id": 2,
                    "timeout": 60,
                    "keepAwake":self._keepAwake,
                    "devType": self.device['DTY'],
                    "toQuery": query
                    }
                }
        
                
        self._lastLCR.append(lcr)
        self._sendRequest(lcr)

    def _processNoReply(self):
        self.logger.debug("No Reply with in timeouts")
        # ask user to press pair button and try again?
        
        if tkMessageBox.askyesno("Communications Timeout",
                                 ("No replay from the Server, \n"
                                  "To try again \n"
                                  "click yes"
                                  )
                                 ):
            self._displayProgress()
            self._starttime = time()
            self._replyCheck()
        else:
            # TODO: we need to cancel the LCR with the core
            # TODO: UDP JSON has no cancle but it will time out
            #            self._lcm.cancelLCR()
            pass
            
    def _sendRequest(self, lcr):
        self.logger.debug("Sending Request to LCMC")
        self._displayProgress()
        self._starttime = time()
        self.qUDPSend.put(json.dumps(lcr))
        self._replyCheck()
    
    def _replyCheck(self):
        # look for a reply
        # TODO: wait on UDP reply (how long)
        if self.qLCRReply.empty():
            if time()-self._starttime > self._lastLCR[-1]['data']['timeout']+10:
                # if timeout passed, let user know no reply
                # close wait diag
                self.progressWindow.destroy()
                self.master.children[self._currentFrame].children['next'].config(state=tk.ACTIVE)
                self._processNoReply()
            else:
                # update wait diag and check again
                self.master.after(500, self._replyCheck)
        else:
            # close wait diag and return reply
            self.progressWindow.destroy()
            self.master.children[self._currentFrame].children['next'].config(state=tk.ACTIVE)
            self.logger.debug("processing reply(replyCheck)")
            self._processReply()
    
    def _buildGrid(self, frame, quit=True, halfSize=False):
        self.logger.debug("Building Grid for {}".format(frame.winfo_name()))
        canvas = tk.Canvas(frame, bd=0, width=self._widthMain-4,
                               height=self._rowHeight, highlightthickness=0)
        canvas.grid(row=0, column=0, columnspan=6)
        
        if halfSize:
            rows=self._rows/2
        else:
            rows=self._rows
        for r in range(rows):
            for c in range(6):
                tk.Canvas(frame, bd=0, #bg=("black" if r%2 and c%2 else "gray"),
                          highlightthickness=0,
                          width=(self._widthMain-4)/6,
                          height=self._rowHeight
                          ).grid(row=r, column=c)
        if (quit):
            tk.Button(frame, text='Quit', command=self._endConfigMe
                      ).grid(row=rows-2, column=0, sticky=tk.E)

    # TODO: UDP JSON debug window
    def _jsonWindowDebug(self):
        self.logger.debug("Setting up JSON debug window")
        self.serialWindow = tk.Toplevel(self.master)
        self.serialWindow.geometry(
               "{}x{}+{}+{}".format(self._widthSerial,
                                    self._heightSerial,
                                    int(self.config.get('LLAPCM',
                                                        'window_width_offset')
                                        )+self._widthMain+20,
                                    self.config.get('LLAPCM',
                                                    'window_height_offset')
                                    )
                                   )
        self.serialWindow.title("LLAP Config Me JSON Debug")
    
        self.serialDebugText = tk.Text(self.serialWindow, state=tk.DISABLED,
                                       relief=tk.RAISED, borderwidth=2,
                                       )
        self.serialDebugText.pack()
        self.serialDebugText.tag_config('TX', foreground='red')
        self.serialDebugText.tag_config('RX', foreground='blue')
        self._serialDebugUpdate()
    
    def _serialDebugUpdate(self):
        # TODO: nice formation for JSON's?
        if not self.qJSONDebug.empty():
            txt = self.qJSONDebug.get()
            self.serialDebugText.config(state=tk.NORMAL)
            self.serialDebugText.insert(tk.END, txt[0]+"\n", txt[1])
            self.serialDebugText.see(tk.END)
            self.serialDebugText.config(state=tk.DISABLED)
            self.qJSONDebug.task_done()
        
        self.master.after(2, self._serialDebugUpdate)
    
    def _endConfigMe(self):
        self.logger.debug("End Client")
        position = self.master.geometry().split("+")
        self.config.set('LLAPCM', 'window_width_offset', position[1])
        self.config.set('LLAPCM', 'window_height_offset', position[2])
        self.master.destroy()
        self._running = False

    def _cleanUp(self):
        self.logger.debug("Clean up and exit")
        # if we were talking to a device we should send a CONFIGEND
        # TODO: send JSON in stead
        
        if self._keepAwake:
            self.logger.debug("Stopping keepAwake")
            self._keepAwake = 0
            query = [{'command': "CONFIGEND"}]
            lcr = {"type": "LCR",
                    "network":self.device['network'],
                    "data":{
                        "id": 4,
                        "keepAwake":self._keepAwake,
                        "timeout": 30,                  # short time out on this one
                        "devType": self.device['DTY'],
                        "toQuery": query
                        }
                    }
            self.logger.debug("Sending ConfigEnd LCMC")
            self._starttime = time()
            self.qUDPSend.put(json.dumps(lcr))
            while self.qLCRReply.empty() and time()-self._starttime < 15:
                sleep(0.1)
    
        # cancle anything outstanding
        # TODO: we have no cancle, we have time outs
        # self._lcm.cancelLCR()
        # disconnect resources
        # TODO: close scokets
        # self._lcm.disconnect_transport()
        self._writeConfig()
        self.tUDPSendStop.set()
        self.tUDPSend.join()
        self.tUDPListenStop.set()
        self.tUDPListen.join()
    
    def _checkArgs(self):
        self.logger.debug("Parse Args")
        parser = argparse.ArgumentParser(description='LLAP Config Me Client')
        parser.add_argument('-d', '--debug',
                            help='Enable debug output to console, overrides LAPCM.cfg setting',
                            action='store_true')
        parser.add_argument('-l', '--log',
                            help='Override the debug logging level, DEBUG, INFO, WARNING, ERROR, CRITICAL'
                            )
        
        self.args = parser.parse_args()

    def _readConfig(self):
        self.logger.debug("Reading Config")
        
        self.config = ConfigParser.SafeConfigParser()
        
        # load defaults
        try:
            self.config.readfp(open(self._configFileDefault))
        except:
            self.logger.debug("Could Not Load Default Settings File")
        
        # read the user config file
        if not self.config.read(self._configFile):
            self.logger.debug("Could Not Load User Config, One Will be Created on Exit")
        
        if not self.config.sections():
            self.logger.debug("No Config Loaded, Quitting")
            sys.exit()


    def _writeConfig(self):
        self.logger.debug("Writing Config")
        with open(self._configFile, 'wb') as _configFile:
            self.config.write(_configFile)

    def _loadDevices(self):
        self.logger.debug("Loading device List")
        try:
            with open(self.config.get('LLAPCM', 'devFile'), 'r') as f:
                read_data = f.read()
            f.closed
            
            self.devices = json.loads(read_data)['Devices']
    
        except IOError:
            self.logger.debug("Could Not Load DevList File")
            self.devices = [
                            {'id': 0,
                             'Description': 'Error loading DevList file'
                            }]




if __name__ == "__main__":
    app = LLAPCongfigMeClient()
    app.on_excute()