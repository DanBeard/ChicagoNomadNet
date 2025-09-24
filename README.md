$$
\begin{array}{c}
\color{white}\rule{150px}{5px} \\
\color{lightblue}\rule{150px}{20px} \\
\color{red}{\huge ✶} \ {\huge ✶}\  {\huge ✶}\ {\huge ✶} \\
\color{lightblue}\rule{150px}{20px} \\
\color{white}\rule{150px}{5px} \\
\end{array}
$$


# ChicagoNomadNet Reticulum Mesh Network

Welcome to the Chicago Community Reticulum Mesh Network! Join us creating a secure, private and resilient network that thrives even in challenging conditions.

## What is Reticulum?

Reticulum is a cryptography-based networking stack designed to create local and wide-area networks using readily available hardware. It ensures secure, resilient communication, even in environments with high latency and low bandwidth. Find out more at the [official reticulum website](https://reticulum.network/) or check out [this amazing essay and video](https://linuxinabit.codeberg.page/blog/reticulum/) by LinuxInABit 

## Getting Started

To join the Chicago mesh network, consider the following applications

1. **Reticulum MeshChat**: A simple web based GUI powered by the Reticulum Network Stack. It can send and receive messages, files, and audio calls with peers over various mediums, including local networks and LoRa radio with an RNode. 

2. **NomadNet**: A command line user interface that enables messaging and hosting pages, apps and files for other users to download. 

3. **Sideband**: A mobile application for Android, Linux, macOS and Windows. It works over Reticulum networks using LoRa, Packet Radio, WiFi, I2P, Encrypted QR Paper Messages, or anything else Reticulum supports.

## Quickstart Guide

Follow these steps to connect to the Chicago mesh network:

### Connecting via Reticulum MeshChat

1. **Install Reticulum MeshChat**:
   - Download and install Reticulum MeshChat from the [official repository](https://github.com/liamcottle/reticulum-meshchat).

2. **Configure TCP Client Connection**:
   - Launch Reticulum MeshChat and navigate to the "Interfaces" tab.
   - Add a new TCP interface with the following details:
     - **Name**: `Chicago Nomadnet`
     - **Type**: `TCPClientInterface`
     - **Address**: `rns.chicagonomad.net`
     - **Port**: `4242`

3. **Configure Rnode**:
   - Launch Reticulum MeshChat and navigate to the "Interfaces" tab.
   - We use the [popular Rnode Settings](https://github.com/markqvist/Reticulum/wiki/Popular-RNode-Settings) for the US region
   - Add a new Rnode interface with the following details:
     - **Name**: `Rnode`
     - **Type**: `RnodeInterface`
     - **Port**: UsbSerial or Bluetooth port (e.g. /dev/ttyACM0)
     - **Frequency**: `914875000` (914.875 MHz)
     - **Bandwidth**: `125 Khz`
     - **Transmit Power**: Check your hardware docs. For example on the T3-S3 I use 14 dBm 
     - **Spreading Factor**: `8` 
     - **Coding Rate**: `5` 

4. **Connect**:
   - Kill and restart Reticulum MeshChat after any configuration change
   - You should now be part of the Chicago mesh network and can start communicating with other members.

### Connecting via NomadNet

1. **Install NomadNet**:
   - Download and install NomadNet from the [official repository](https://github.com/markqvist/NomadNet) or via pip like the example below:
 ```sh
# Install Nomad Network and dependencies
pip install nomadnet

# Run the client
nomadnet
 ```  


2. **Change Config File**:
   - with nomadnet turned off change the reticulum config file at `~/.reticulum/config
   - Add this entry to the bottom of the file
```yaml
  [[Chicago Nomadnet TCP]]
    type = TCPClientInterface
    interface_enabled = true
    target_host = rns.chicagonomad.net
    target_port = 4242
    name = Chicago Nomadnet TCP
    selected_interface_mode = 1
    configured_bitrate = None

```


3. **Configure Rnode**:
   - with nomadnet turned off change the reticulum config file at `~/.reticulum/config
   - Add this entry to the bottom of the file
   - We use the [popular Rnode Settings](https://github.com/markqvist/Reticulum/wiki/Popular-RNode-Settings) for the US region
```yaml 
  [[RnodeUSB]]
    type = RNodeInterface
    interface_enabled = true
    port = /path/to/usb/or/bluetooh/port
    frequency = 914875000
    bandwidth = 125000
    txpower = 20
    spreadingfactor = 8
    codingrate = 5
    name = RnodeUSB
    selected_interface_mode = 1
    configured_bitrate = None
```


4. **Connect**:
   - Kill and restart nomadnet after any configuration change
   - You should now be part of the Chicago mesh network and can start communicating with other members.
   - If you want to enable offline message delivery then add a preferred propagation node (chicago nomad runs ones at address `afe402b7cd7ee5f5cca82da1963db84d` and turn on auto sync)

For more detailed information and troubleshooting, refer to the [Reticulum documentation](https://reticulum.network/manual/whatis.html).


### Say Hi to locals!

Look for a Nomad Network node named 'chicago_nomad' or navigate to `c95cce570afd2fa1545fa86c07256fdc:/page/index.mu` to find more info.

Look at nodes in the "Announce" tab and pay attention to how nay hops away they are. You can also use the "network visualiser" in Meschat to see who's announced they're online and where they are connected on the network.

### Explore

The chicagonomad.net node is connected to the internet and will route between a few other TCP interfaces. Watch the announce stream or browse Nomad network nodes, and have fun!
You can see the scripts that it hosts here in the repo. They include some projects and random tests. It's all MIT licensed, so copy it, host it, modiy it; make the world a cooler place!


