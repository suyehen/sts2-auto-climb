"""Bounded read-only access to the user's installed Godot pack."""
import hashlib
import struct


class GamePack:
    def __init__(self,path):
        self.path=path
        self.entries={}
        with open(path,'rb') as stream:
            if stream.read(4)!=b'GDPC':raise ValueError('Unsupported pack')
            version,_,_,_,flags=struct.unpack('<5I',stream.read(20))
            base,index=struct.unpack('<QQ',stream.read(16))
            if version!=3 or flags&1:raise ValueError('Unsupported or encrypted pack')
            stream.seek(index);count=struct.unpack('<I',stream.read(4))[0]
            if count>100000:raise ValueError('Invalid pack index')
            for _ in range(count):
                length=struct.unpack('<I',stream.read(4))[0]
                if length>2000:raise ValueError('Invalid resource name')
                name=stream.read(length).rstrip(b'\0').decode('utf-8')
                offset,size=struct.unpack('<QQ',stream.read(16));digest=stream.read(16);entry_flags=struct.unpack('<I',stream.read(4))[0]
                self.entries[name]=(base+offset,size,digest,entry_flags)

    def read(self,name):
        offset,size,digest,flags=self.entries[name]
        if flags or size>30_000_000:raise ValueError('Unsupported resource')
        with open(self.path,'rb') as stream:stream.seek(offset);data=stream.read(size)
        if len(data)!=size or hashlib.md5(data).digest()!=digest:raise ValueError('Resource checksum mismatch')
        return data
