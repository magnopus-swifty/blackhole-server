# Copyright 2024 Magnopus LLC

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     https://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from .base_capture import BaseCaptureThread, logger
from blackhole.constants import *
import blackhole.database_utils as utils
import logging
import struct

FREED_PACKET_SIZE = 29 # All FreeD position/orientation data is sent in 29-byte packets.

class FreeDPacket():
    def __init__(self, packetData : bytes):
        if len(packetData) != FREED_PACKET_SIZE:
            logger.error("FreeD camera transform packet must contain 29 bytes.")
            return

        self.packetBytes = packetData

        # Format string for unpacking the packet byte array:
        packet_format = '!cc3s3s3s3s3s3s3s3s2sc'
        
        (message_type_byte,
          camera_id_byte, 
          pan_bytes, 
          tilt_bytes, 
          roll_bytes, 
          x_bytes, 
          y_bytes, 
          z_bytes, 
          zoom_byte, 
          focus_byte, 
          spare_bytes, 
          checksum_byte) = struct.unpack(packet_format, self.packetBytes)

        self.cam_id = int.from_bytes(camera_id_byte, byteorder='big')
        
        # All rotation values are expressed in degrees ranging from -180 t0 180
        self.rot_pan = self.getFreeDFloat(pan_bytes, 15)
        self.rot_tilt = self.getFreeDFloat(tilt_bytes, 15)
        self.rot_roll = self.getFreeDFloat(roll_bytes, 15)

        # All position values are expressed in millimeters
        self.pos_x = self.getFreeDFloat(x_bytes, 6)
        self.pos_y = self.getFreeDFloat(y_bytes, 6)
        self.pos_z = self.getFreeDFloat(z_bytes, 6)

        # Zoom and focus are unsigned values of arbitrary units to be
        # interpreted by whatever system you use
        self.zoom = int.from_bytes(zoom_byte, byteorder='big')
        self.focus = int.from_bytes(focus_byte, byteorder='big')

        self.spare = int.from_bytes(spare_bytes, byteorder='big')
        self.checksum = int.from_bytes(checksum_byte, byteorder='big')

    def __str__(self):
        return (
            f"CAMERA ID: {self.cam_id} \n"
            f"PAN: {self.rot_pan} \n"
            f"TILT: {self.rot_tilt} \n" 
            f"ROLL: {self.rot_roll} \n"
            f"POSITION: ({self.pos_x},{self.pos_y},{self.pos_z}) \n"
            f"ZOOM: {self.zoom} \n"
            f"FOCUS: {self.focus} \n"
            f"CHECKSUM: {hex(self.checksum)} \n"
            f"CHECKSUM VALID: {self.checksumValid()} \n"
        )

    def getFreeDFloat(self, rawBytes : bytes, fractionalByteSize : int):
        nonFractional = int.from_bytes(rawBytes, byteorder='big', signed = True)
        fractional = nonFractional / float(1 << fractionalByteSize)

        return fractional

    def checksumValid(self):
        '''
        The checksum of FreeD packets is calculated by subtracting (mod 256)
        each byte of the packet from 0x40. Including the checksum byte itself
        in this calculation should result in a value of 0 if the packet is valid.
        '''

        sum = 0x40

        for byte in self.packetBytes:
            sum = (sum - byte) & 0x3 # Take mod 256 each time

        return sum == 0
    
class FreeDCaptureThread(BaseCaptureThread):
    @property
    def packet_size(self):
        return FREED_PACKET_SIZE

    def packageFrameData(self, packet : FreeDPacket):
        data = dict()

        # Convert the camera transform from FreeD's conventions to USD
        # FreeD uses a right-handed coordinate system with Z-up, with positions in millimeters
        # USD uses a right-handed coordinate system with Y-up, with metersPerUnit set to 0.01 (aka cm)

        # Swap the axes and scale from mm to cm
        data[TRACKING_X] = packet.pos_y / 10.0
        data[TRACKING_Y] = packet.pos_z / 10.0
        data[TRACKING_Z] = packet.pos_x / 10.0

        # Tilt and roll are equivalent to pitch and roll respectively
        # Yaw is negated and rotated by 90 degrees to account for the different axis conventions
        data[TRACKING_PITCH] = packet.rot_tilt
        data[TRACKING_YAW] = -(packet.rot_pan + 90)
        data[TRACKING_ROLL] = packet.rot_roll

        data[TRACKING_TIMECODE_KEY] = utils.getSystemTimecodeAsFrames(self.frameRate)
        
        return data

    def parsePacket(self, packetBytes):
        if packetBytes[0:1] == b"\xd1":
            transformPacketObject = FreeDPacket(packetBytes)
            logger.info(transformPacketObject)

            return transformPacketObject
        else:
            return None
        
    def validateParsedData(self, parsedPacket):
        if parsedPacket == None or not isinstance(parsedPacket, FreeDPacket):
            return False
        else:
            return parsedPacket.checksumValid()
        
    def cacheParsedData(self, parsedPacket):
        transformData = self.packageFrameData(parsedPacket)
        self.capturedTrackingData.append(transformData)