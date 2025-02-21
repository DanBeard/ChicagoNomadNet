# RNS QR code reader. Ultimate goal: read QR codes from image streams or webcams and drop them on the network

from qreader import QReader
import cv2
import base64
import asyncio
import RNS
from LXMF import LXMessage, LXMRouter


class TransparentDestination(RNS.Destination):
    # we're already encrypted so skip it
    def encrypt(self, plaintext):
         return plaintext

class QrRouter:
    
    def __init__(self, display_name):
        self.r = RNS.Reticulum()
        self.router = LXMRouter(storagepath="./tmp2")
        #router.register_delivery_callback(self.on_rns_recv)
        self.ident = RNS.Identity() # todo static Identity?
        self.source = self.router.register_delivery_identity(self.ident, display_name=display_name)
        self.router.announce(self.source.hash)
        self._msg_queue = []
        print("INITIED!")

    def validate_and_enqueue_msg(self, uri):
        if uri is None:
            return
        
        if not uri.lower().startswith(LXMessage.URI_SCHEMA+"://"):
                RNS.log("Cannot ingest LXM, invalid URI provided.", RNS.LOG_ERROR)
                return

        lxmf_data = base64.urlsafe_b64decode(uri.replace(LXMessage.URI_SCHEMA+"://", "").replace("/", "")+"==")
        #transient_id = RNS.Identity.full_hash(lxmf_data)
        destination_hash  = lxmf_data[:LXMessage.DESTINATION_LENGTH]
        data_data = lxmf_data[LXMessage.DESTINATION_LENGTH:]
        self._msg_queue.append((destination_hash, data_data))
            
    async def run_delivery_loop(self):
        while True:
            queue = self._msg_queue
            self._msg_queue = []
            for destination_hash, lxmf_data in queue:
                dest_id = RNS.Identity.recall(destination_hash)
            
                if dest_id is not None and RNS.Transport.has_path(destination_hash):
                    dest = TransparentDestination(dest_id, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
                    packet = RNS.Packet(dest, lxmf_data)
                    status = packet.send()
                    print("sent to...."+dest.hexhash)
                    print(status.proved)
                    print(status.get_status())
                    await asyncio.sleep(0.01) # small sleep so we don't ddos with big queue
                else:
                    print("re-queing")
                    RNS.Transport.request_path(destination_hash)
                    # requeue until we have a path
                    self._msg_queue.append((destination_hash, lxmf_data))
                  
            await asyncio.sleep(5)
                    
   
class QrIngest:
    
    def __init__(self, qr_router):
        self.qreader = QReader()
        self.qr_router = qr_router
        
    async def run_ingest_loop(self):
        while True:
            
            await asyncio.sleep(5)
                     
    
    
    

if __name__ == "__main__":
    # Get the image that contains the QR code
    qrouter = QrRouter("QrRouter")
    qr_ingest = QrIngest(qrouter)
    
    image = cv2.cvtColor(cv2.imread("/home/v/test_paper_msg.png"), cv2.COLOR_BGR2RGB)
    decoded_text = qreader.detect_and_decode(image=image)
    for uri in decoded_text:
        qrouter.validate_and_enqueue_msg(uri)
        
    loop = asyncio.get_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(qrouter.run_delivery_loop())
    #loop.create_task(proxy())
    loop.run_forever()
        
    