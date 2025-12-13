# Example 
from zimscan import Reader

path = "wikipedia_en_all_nopic_2019-10.zim"
with Reader(open(path, "rb"), skip_metadata=True) as reader:
    for record in reader:
        data = record.read()




# Here is the Record class for reference. Notice mimet_type and title and that it is readbale

import io


class Record(io.RawIOBase):
    """ZIM content entry.

    This is a binary, read-only and non-seekable file-like. It is invalidated when the
    next record is requested.

    """

    def __init__(
        self,
        file,
        length,
        mime_type=None,
        namespace=None,
        url=None,
        title=None,
        revision=0,
    ):
        self.length = length
        self._file = file
        self._remaining = length
        self.mime_type = mime_type
        self.namespace = namespace
        self.url = url
        self.title = title
        self.revision = revision

    def readable(self):
        return True

    def readinto(self, buffer):

        # Check that we are still allowed to read
        if self._file is None or self.closed:
            raise RuntimeError("Cannot read from invalidated record")

        # Make sure we do not read more than our blob
        requested = len(buffer)
        if requested == 0 or self._remaining == 0:
            return 0
        if requested > self._remaining:
            buffer = memoryview(buffer)[: self._remaining]

        # Ask underlying file for content
        size = self._file.readinto(buffer)
        self._remaining -= size
        return size