#!/usr/bin/env python3
"""
RNS Server Bridge - Accept incoming Reticulum connections and forward to local TCP/UDP server
"""

import RNS
import socket
import threading
import time
import argparse
import logging
import sys
from typing import Dict, Optional, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('rns_server_bridge.log')
    ]
)
logger = logging.getLogger(__name__)

class ServerBridge:
    def __init__(self, target_host: str, target_port: int, protocol: str, 
                 timeout: int = 900, service_name: str = "bridge_service", 
                 identity_file: str = "./bridge_ident"):
        """
        Initialize the RNS Server Bridge
        
        Args:
            target_host: Local TCP/UDP server IP to forward to
            target_port: Local TCP/UDP server port to forward to
            protocol: 'tcp' or 'udp'
            timeout: Connection timeout in seconds (default: 15 minutes)
            service_name: RNS service name
            identity_file: Path to identity file (default: ./bridge_ident)
        """
        self.target_host = target_host
        self.target_port = target_port
        self.protocol = protocol.lower()
        self.timeout = timeout
        self.service_name = service_name
        self.identity_file = identity_file
        
        # Track active connections: RNS link -> (socket, last_activity)
        self.connections: Dict[RNS.Link, Tuple[socket.socket, float]] = {}
        self.connection_lock = threading.Lock()
        
        # Initialize RNS
        RNS.Reticulum()
        
        # Load or create persistent identity
        self.identity = self._load_or_create_identity()
        logger.info(f"Using identity: {RNS.prettyhexrep(self.identity.hash)}")
        
        # Create destination
        self.destination = RNS.Destination(
            self.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            "bridge",
            service_name
        )
        
        # Set link established callback
        self.destination.set_link_established_callback(self.client_connected)
        
        # Start cleanup thread
        self.cleanup_thread = threading.Thread(target=self._cleanup_connections, daemon=True)
        self.cleanup_thread.start()
        
        logger.info(f"Server bridge initialized")
        logger.info(f"Target: {protocol.upper()} {target_host}:{target_port}")
        logger.info(f"RNS Destination: {RNS.prettyhexrep(self.destination.hash)}")
        logger.info(f"Identity file: {identity_file}")
        logger.info(f"Timeout: {timeout} seconds")


    def _load_or_create_identity(self) -> RNS.Identity:
        """Load existing identity or create a new one"""
        try:
            # Try to load existing identity
            identity = RNS.Identity.from_file(self.identity_file)
            if identity is None:
                raise RuntimeError("IDent is none :( )")
            logger.info(f"Loaded existing identity from {self.identity_file}")
            return identity
        except Exception as e:
            logger.warning(f"Could not load identity from {self.identity_file}: {e}")
        
        # Create new identity if loading failed or file doesn't exist
        logger.info(f"Creating new identity and saving to {self.identity_file}")
        identity = RNS.Identity()
        
        try:
            identity.to_file(self.identity_file)
            logger.info(f"Saved new identity to {self.identity_file}")
        except Exception as e:
            logger.error(f"Failed to save identity to {self.identity_file}: {e}")
            logger.warning("Identity will not persist across restarts")
        
        return identity
    
    def client_connected(self, link: RNS.Link):
        """Handle new RNS client connections"""
        logger.info(f"New RNS client connected: {link}")
        
        try:
            # Create socket to target server
            if self.protocol == 'tcp':
                target_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                target_socket.connect((self.target_host, self.target_port))
            else:  # UDP
                target_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                # For UDP, we don't connect but store the target address
            
            # Store connection
            with self.connection_lock:
                self.connections[link.hash] = (target_socket, time.time())
            
            # Set packet callback for this link
            link.set_packet_callback(lambda data, packet, link=link: self.rns_data_received(data, packet, link))
            
            # Start thread to handle data from target socket
            socket_thread = threading.Thread(
                target=self._handle_target_socket,
                args=(link, target_socket),
                daemon=True
            )
            socket_thread.start()
            
            logger.info(f"Established bridge for client {link}")
            
        except Exception as e:
            logger.error(f"Failed to establish bridge for client: {e}")
            link.teardown()

    def rns_data_received(self, data: bytes, packet, link: RNS.Link):
        """Handle data received from RNS client"""
        try:
            print("Data recv:", data, "     ", link)
            with self.connection_lock:
                if link.hash in  self.connections:
                    target_socket, _ = self.connections[link.hash]
                    # Update last activity
                    self.connections[link.hash] = (target_socket, time.time())
                    
                    if self.protocol == 'tcp':
                        target_socket.send(data)
                    else:  # UDP
                        target_socket.sendto(data, (self.target_host, self.target_port))
                    
                    logger.debug(f"Forwarded {len(data)} bytes from RNS to target")
                else:
                    logger.error(f"Recv Data from unknown link! {link}")
                    
        except Exception as e:
            logger.error(f"Error forwarding RNS data to target: {e}")
            self._cleanup_connection(link)

    def _handle_target_socket(self, link: RNS.Link, target_socket: socket.socket):
        """Handle data from target socket back to RNS"""
        try:
            target_socket.settimeout(10.0)  # Non-blocking with timeout
            
            while link.status == RNS.Link.ACTIVE:
                try:
                    if self.protocol == 'tcp':
                        data = target_socket.recv(4096)
                        if not data:
                            continue
                    else:  # UDP
                        data, _ = target_socket.recvfrom(4096)
                    
                    # Send data back over RNS
                    print("Data send:", data, "     ", link)
                    packet = RNS.Packet(link, data)
                    packet.send()
                    
                    # Update last activity
                    with self.connection_lock:
                        if link.hash in  self.connections:
                            self.connections[link.hash] = (target_socket, time.time())
                    
                    logger.debug(f"Forwarded {len(data)} bytes from target to RNS")
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.error(f"Error receiving from target socket: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"Error in target socket handler: {e}")
        finally:
            self._cleanup_connection(link)

    def _cleanup_connection(self, link: RNS.Link):
        """Clean up a specific connection"""
        with self.connection_lock:
            if link.hash in  self.connections:
                target_socket, _ = self.connections[link.hash]
                try:
                    target_socket.close()
                except:
                    pass
                del self.connections[link.hash]
                logger.info(f"Cleaned up connection for {link}")

    def _cleanup_connections(self):
        """Periodic cleanup of inactive connections"""
        while True:
            try:
                current_time = time.time()
                to_cleanup = []
                
                with self.connection_lock:
                    for link, (target_socket, last_activity) in self.connections.items():
                        if current_time - last_activity > self.timeout:
                            to_cleanup.append(link)
                
                for link in to_cleanup:
                    logger.info(f"Connection timeout for {link}")
                    link.teardown()
                    self._cleanup_connection(link)
                
                time.sleep(60)  # Check every 60 seconds
                
            except Exception as e:
                logger.error(f"Error in cleanup thread: {e}")
                time.sleep(30)

    def start(self):
        """Start the server bridge"""
        logger.info("RNS Server Bridge started")
        logger.info(f"Announce destination: {RNS.prettyhexrep(self.destination.hash)}")
        
        # Announce the destination
        self.destination.announce()
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down server bridge...")
            self.shutdown()

    def shutdown(self):
        """Shutdown the server bridge"""
        logger.info("Shutting down all connections...")
        
        with self.connection_lock:
            for link, (target_socket, _) in self.connections.items():
                try:
                    target_socket.close()
                    link.teardown()
                except:
                    pass
            self.connections.clear()
        
        logger.info("Server bridge shutdown complete")


def main():
    parser = argparse.ArgumentParser(description='RNS Server Bridge')
    parser.add_argument('target_host', nargs='?', help='Target server IP address', default="127.0.0.1")
    parser.add_argument('target_port', nargs='?', type=int, help='Target server port', default=22)
    parser.add_argument('protocol', nargs='?', choices=['tcp', 'udp'], help='Protocol (tcp or udp)', default="tcp")
    parser.add_argument('--timeout', type=int, default=900, 
                       help='Connection timeout in seconds (default: 900)')
    parser.add_argument('--service', default='bridge_service',
                       help='RNS service name (default: bridge_service)')
    parser.add_argument('--identity', default='./bridge_ident',
                       help='Identity file path (default: ./bridge_ident)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if True or args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        bridge = ServerBridge(
            target_host=args.target_host,
            target_port=args.target_port,
            protocol=args.protocol,
            timeout=args.timeout,
            service_name=args.service
        )
        bridge.start()
        
    except Exception as e:
        logger.error(f"Failed to start server bridge: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()