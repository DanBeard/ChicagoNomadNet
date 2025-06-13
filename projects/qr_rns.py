# RNS QR code reader. Ultimate goal: read QR codes from image streams or webcams and drop them on the network

from qreader import QReader
import cv2
import base64
import asyncio
import RNS
import time
import os
import numpy as np
from LXMF import LXMessage, LXMRouter


webcam_urls = ["https://cdns.abclocal.go.com/three/wls/webcam/StateSt_cap.jpg"]

qreader = QReader()

class TransparentDestination(RNS.Destination):
    # we're already encrypted so skip it
    def encrypt(self, plaintext):
         return plaintext

class QrRouter:
    
    help_text = "Hello, if you send a message with an attached image file. "\
        "I will search the image for QR codes of an LXMessage, and attempt to deliver it"
                                    
    
    def __init__(self, display_name):
        self.r = RNS.Reticulum()
        self.router = LXMRouter(storagepath="./tmp2")
        self.router.register_delivery_callback(self.on_rns_recv)
        
         # ensure provided storage dir exists, or the default storage dir exists
        base_storage_dir = os.path.join("storage")
        os.makedirs(base_storage_dir, exist_ok=True)

        # configure path to default identity file
        default_identity_file = os.path.join(base_storage_dir, "identity")

        # if default identity file does not exist, generate a new identity and save it
        if not os.path.exists(default_identity_file):
            identity = RNS.Identity(create_keys=True)
            with open(default_identity_file, "wb") as file:
                file.write(identity.get_private_key())
            print("Reticulum Identity <{}> has been randomly generated and saved to {}.".format(identity.hash.hex(), default_identity_file))

        # default identity file exists, load it
        identity = RNS.Identity(create_keys=False)
        identity.load(default_identity_file)
        print("Reticulum Identity <{}> has been loaded from file {}.".format(identity.hash.hex(), default_identity_file))
        
        self.ident = identity
        self.source = self.router.register_delivery_identity(self.ident, display_name=display_name)
        self.router.announce(self.source.hash)
        self._msg_queue = []
        self._response_queue = []
        
    def process_img(self, buf, reply_hash=None):
        image = cv2.imdecode(np.frombuffer(buf, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return 0
        
        decoded_text = qreader.detect_and_decode(image=image)
        num_sent = 0
        for uri in decoded_text:
            num_sent+=1
            self.validate_and_enqueue_msg(uri, reply_hash)
        return num_sent
                    
    def on_rns_recv(self, message):        
        # DO STUFF WITH MESSAGE HERE
        reply_hash = message.source_hash
        has_attachment = message.fields is not None and len(message.fields) > 0
        if has_attachment:
            files = [x[1] for x in message.fields.values() if len(x) > 0 and len(x[1]) > 5]
            num_sent = 0
            for f in files:
                if type(f) == str:
                    f = f.encode()
                    
            num_sent = self.process_img(f, reply_hash=reply_hash)
                    
            if num_sent == 0:
                print("got attachment, but none were an image")
                if len(files) > 0:
                    self._response_queue.append((reply_hash, "No QR codes found in attached image."))
                else:
                    # weird bug where sideband send 3 null bytes as a fields attachment, but meshchat doesnt
                    self._response_queue.append((reply_hash, self.help_text))
                    
            else:
                self._response_queue.append((reply_hash, f"{num_sent} message(s) queued for delivery"))
                     
        else:
            print("msg from", reply_hash.hex())
            RNS.Transport.request_path(reply_hash)
            self._response_queue.append((reply_hash, self.help_text))
           
            
    def validate_and_enqueue_msg(self, uri, ack_hash=None):
        if uri is None:
            return
        
        if not uri.lower().startswith(LXMessage.URI_SCHEMA+"://"):
                RNS.log("Cannot ingest LXM, invalid URI provided.", RNS.LOG_ERROR)
                return

        lxmf_data = base64.urlsafe_b64decode(uri.replace(LXMessage.URI_SCHEMA+"://", "").replace("/", "")+"==")
        #transient_id = RNS.Identity.full_hash(lxmf_data)
        destination_hash  = lxmf_data[:LXMessage.DESTINATION_LENGTH]
        data_data = lxmf_data[LXMessage.DESTINATION_LENGTH:]
        self._msg_queue.append((destination_hash, data_data, ack_hash))
            
    async def run_delivery_loop(self):
        last_announce = 0
        while True:
            queue = self._msg_queue
            self._msg_queue = []
        
            for destination_hash, lxmf_data, ack_hash in queue:
                dest_id = RNS.Identity.recall(destination_hash)
                if dest_id is not None and RNS.Transport.has_path(destination_hash):
                    dest = TransparentDestination(dest_id, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
                    packet = RNS.Packet(dest, lxmf_data)
                    status = packet.send()
                    print("sent to...."+dest.hexhash)
                    if ack_hash is not None:
                        self._response_queue.append((ack_hash, "Message delivered!"))
                    await asyncio.sleep(0.01) # small sleep so we don't ddos with big queue
                else:
                    print("re-queing")
                    RNS.Transport.request_path(destination_hash)
                    # requeue until we have a path
                    self._msg_queue.append((destination_hash, lxmf_data, ack_hash))
                  
            # help queue for responding with help to messages
            r_q = self._response_queue
            self._response_queue = []
            for reply_hash, text in r_q:
                dest_id = RNS.Identity.recall(reply_hash)
                if dest_id is not None and RNS.Transport.has_path(reply_hash):
                    destination = RNS.Destination(dest_id, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
                    lxm = LXMessage(destination, self.source,
                                    text,
                                    "QR Router Message",
                                    desired_method=LXMessage.OPPORTUNISTIC)
            
                    self.router.handle_outbound(lxm)
                    print(" -> " + str(text))
                else:
                    RNS.Transport.request_path(reply_hash)
                    self._response_queue.append((reply_hash, text))
                    
            # announce when it's time
            now = time.time()
            if now - last_announce > 30*60:
                print("announcing again!")
                self.router.announce(self.source.hash)
                last_announce = now
                
            await asyncio.sleep(2.5)
                    
import aiohttp
class QrIngest:
    
    def __init__(self, qr_router: QrRouter):
        self.qr_router = qr_router
        
    async def run_ingest_loop(self):
        await asyncio.sleep(2) # warmup time
        while True:
            async with aiohttp.ClientSession() as sess:
                for url in webcam_urls:
                    cache_bust_url = url+"?cache_bust="+str(time.time())
                    async with sess.get(cache_bust_url) as response:
                        if response.ok:
                            data = await response.read()
                            num_sent = self.qr_router.process_img(data)
                            if num_sent > 0:
                                print("!!!Found QR code in: "+cache_bust_url)
                            
                        else:
                            print("Bad status from "+ str(cache_bust_url))
                            print(response.status)
           
                
            # grab new images to process from webcams or other streams
            await asyncio.sleep(60)
                     

if __name__ == "__main__":
    # Get the image that contains the QR code
    qrouter = QrRouter("QrRouter [Bot]")
    qr_ingest = QrIngest(qrouter)
    
        
    loop = asyncio.get_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(qrouter.run_delivery_loop())
    loop.create_task(qr_ingest.run_ingest_loop())
    #loop.create_task(proxy())
    loop.run_forever()
        
    