import requests
import struct
import zlib
from typing import Tuple
import os
import concurrent.futures
import threading
from urllib.parse import urlparse, unquote
import ntpath
import traceback
import requests

def resolve_github_download_url(url: str) -> str:
    """Try to resolve GitHub release URLs to their actual download URLs"""
    if 'github.com' in url and '/releases/download/' in url:
        print("Detected GitHub release URL - attempting to resolve actual download URL...")
        
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        try:
            response = session.head(url, allow_redirects=True)
            resolved_url = response.url
            
            print(f"Resolved GitHub URL:")
            print(f"  Original: {url}")
            print(f"  Resolved: {resolved_url}")
            
            test_response = session.get(resolved_url, headers={'Range': 'bytes=0-0'})
            if test_response.status_code == 206:
                print("Resolved URL supports range requests!")
                return resolved_url
            else:
                print(f"Resolved URL doesn't support range requests (status: {test_response.status_code})")
                return url
                
        except Exception as e:
            print(f"Failed to resolve GitHub URL: {e}")
            return url
    
    return url

def get_filename_from_headers(url: str) -> str:
    """Get the actual filename from HTTP headers (Content-Disposition) or URL fallback"""
    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0'
        })
        r = session.head(url, allow_redirects=True, timeout=10)
        cd = r.headers.get('content-disposition')
        if cd:
            import re
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', cd, re.IGNORECASE)
            if m:
                raw_name = m.group(1)
                # URL decode filename
                decoded_name = unquote(raw_name)
                # Remove utf-8'' prefix if still present
                if decoded_name.lower().startswith("utf-8''"):
                    decoded_name = decoded_name[7:]
                return decoded_name
        final = urlparse(r.url).path
        name = ntpath.basename(final)
        if name:
            return name
    except Exception:
        pass
    # fallback to original URL basename or "download"
    p = urlparse(url).path
    return ntpath.basename(p) or "ArKT-Magic"

class FixedRemoteZipReader:
    def __init__(self, url: str):
        self.original_url = url
        self.url = resolve_github_download_url(url)
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        self.file_size = 0
        self.files_info = {}
        self.actual_url = None
        self._initialize()
    
    def _initialize(self):
        """Initialize by getting file size and checking range support"""
        try:
            print("Initializing remote ZIP reader...")
            
            response = self.session.head(self.url, allow_redirects=True)
            self.actual_url = response.url
            
            if self.actual_url != self.url:
                print(f"Following redirect to: {self.actual_url}")
            
            test_response = self.session.get(self.actual_url, headers={'Range': 'bytes=0-0'})
            
            if test_response.status_code == 501:
                print("\n" + "="*70)
                print("ERROR: SERVER DOESN'T SUPPORT RANGE REQUESTS")
                print("="*70)
                print("The server returned 501 Not Implemented for range requests.")
                print("This means we cannot read the ZIP file remotely.")
                raise Exception("Server doesn't support HTTP range requests (501)")
            elif test_response.status_code != 206:
                print(f"Warning: Range request returned status {test_response.status_code}")
            
            if 'content-length' in response.headers:
                self.file_size = int(response.headers['content-length'])
                print(f"Remote file size: {self.file_size:,} bytes ({self.file_size / (1024*1024):.1f} MB)")
            elif test_response.status_code == 206 and 'content-range' in test_response.headers:
                content_range = test_response.headers['content-range']
                self.file_size = int(content_range.split('/')[-1])
                print(f"Remote file size: {self.file_size:,} bytes ({self.file_size / (1024*1024):.1f} MB)")
            else:
                raise Exception("Cannot determine file size")
                
        except Exception as e:
            if "501" in str(e):
                raise e
            raise Exception(f"Failed to initialize remote file: {e}")
    
    def _read_bytes(self, start: int, length: int) -> bytes:
        """Read specific byte range from remote file"""
        end = start + length - 1
        headers = {'Range': f'bytes={start}-{end}'}
        
        try:
            response = self.session.get(self.actual_url, headers=headers)
            if response.status_code not in [206, 200]:
                raise Exception(f"Range request failed with status {response.status_code}")
            return response.content
        except Exception as e:
            raise Exception(f"Failed to read bytes {start}-{end}: {e}")
    
    # CORRECTED ZIP64 implementation for your Python script
    # Replace your existing _find_zip64_end_of_central_directory method with this:

    def _find_zip64_end_of_central_directory(self, eocd_offset: int) -> Tuple[int, int]:
        """Find ZIP64 End of Central Directory record - CORRECTED VERSION"""
        
        # According to ZIP spec, ZIP64 locator is exactly 20 bytes before EOCD
        zip64_locator_offset = eocd_offset - 20
        
        print(f"Looking for ZIP64 locator at offset: {zip64_locator_offset}")
        
        if zip64_locator_offset < 0:
            raise Exception("ZIP64 locator would be at negative offset")
        
        # Read ZIP64 End of Central Directory Locator (20 bytes)
        try:
            locator_data = self._read_bytes(zip64_locator_offset, 20)
        except Exception as e:
            raise Exception(f"Failed to read ZIP64 locator: {e}")
        
        # Check ZIP64 locator signature: 0x07064b50
        signature = struct.unpack('<L', locator_data[0:4])[0]
        
        if signature != 0x07064b50:  # Note: 0x07064b50, not 0x06074b50
            print(f"ZIP64 locator signature not found. Got: 0x{signature:08x}")
            # Some ZIP files may not have ZIP64 locator even with 0xFFFFFFFF offset
            # Try to find it by searching backwards
            return self._search_zip64_locator_fallback()
        
        print("Found ZIP64 locator signature!")
        
        # Parse ZIP64 locator: signature(4) + disk(4) + offset(8) + total_disks(4)
        signature, disk_num, zip64_eocd_offset, total_disks = struct.unpack('<LLQL', locator_data)
        
        print(f"ZIP64 EOCD offset: {zip64_eocd_offset}")
        print(f"Disk number: {disk_num}")
        print(f"Total disks: {total_disks}")
        
        # Read ZIP64 End of Central Directory record
        try:
            # First read the header to get the size
            zip64_eocd_header = self._read_bytes(zip64_eocd_offset, 12)
            signature, eocd64_size = struct.unpack('<LQ', zip64_eocd_header)
            
            if signature != 0x06064b50:
                raise Exception(f"Invalid ZIP64 EOCD signature: 0x{signature:08x}")
            
            print(f"ZIP64 EOCD size: {eocd64_size}")
            
            # Read the full ZIP64 EOCD record
            full_zip64_eocd = self._read_bytes(zip64_eocd_offset, min(int(eocd64_size) + 12, 1024))
            
            # Parse ZIP64 EOCD fields
            # Format: signature(4) + size(8) + version_made(2) + version_need(2) + 
            #         disk_num(4) + cd_disk(4) + cd_entries_disk(8) + cd_entries_total(8) + 
            #         cd_size(8) + cd_offset(8)
            
            if len(full_zip64_eocd) < 56:
                raise Exception("ZIP64 EOCD record too short")
            
            cd_entries_total = struct.unpack('<Q', full_zip64_eocd[32:40])[0]
            cd_size = struct.unpack('<Q', full_zip64_eocd[40:48])[0] 
            cd_offset = struct.unpack('<Q', full_zip64_eocd[48:56])[0]
            
            print(f"ZIP64 Central Directory entries: {cd_entries_total}")
            print(f"ZIP64 Central Directory size: {cd_size:,} bytes")
            print(f"ZIP64 Central Directory offset: {cd_offset}")
            
            return int(cd_offset), int(cd_size)
            
        except Exception as e:
            raise Exception(f"Failed to read ZIP64 EOCD: {e}")

    def _search_zip64_locator_fallback(self) -> Tuple[int, int]:
        """Fallback method to search for ZIP64 locator if not at expected position"""
        print("Searching for ZIP64 locator using fallback method...")
        
        zip64_locator_sig = b'\x50\x4b\x07\x06'  # Note: correct signature bytes
        search_size = min(8192, self.file_size)
        start_pos = max(0, self.file_size - search_size)
        data = self._read_bytes(start_pos, search_size)
        
        zip64_locator_pos = None
        for i in range(len(data) - 20, -1, -1):
            if data[i:i+4] == zip64_locator_sig:
                zip64_locator_pos = start_pos + i
                print(f"Found ZIP64 locator at offset: {zip64_locator_pos}")
                break
        
        if zip64_locator_pos is None:
            raise Exception("ZIP64 End of Central Directory Locator not found in fallback search")
        
        # Read and parse the locator
        locator_data = self._read_bytes(zip64_locator_pos, 20)
        signature, disk_num, zip64_eocd_offset, total_disks = struct.unpack('<LLQL', locator_data)
        
        # Continue with ZIP64 EOCD reading...
        zip64_eocd_header = self._read_bytes(zip64_eocd_offset, 12)
        signature, eocd64_size = struct.unpack('<LQ', zip64_eocd_header)
        
        if signature != 0x06064b50:
            raise Exception(f"Invalid ZIP64 EOCD signature: 0x{signature:08x}")
        
        full_zip64_eocd = self._read_bytes(zip64_eocd_offset, min(int(eocd64_size) + 12, 1024))
        
        if len(full_zip64_eocd) < 56:
            raise Exception("ZIP64 EOCD record too short")
        
        cd_entries_total = struct.unpack('<Q', full_zip64_eocd[32:40])[0]
        cd_size = struct.unpack('<Q', full_zip64_eocd[40:48])[0] 
        cd_offset = struct.unpack('<Q', full_zip64_eocd[48:56])[0]
        
        print(f"ZIP64 Central Directory entries: {cd_entries_total}")
        print(f"ZIP64 Central Directory size: {cd_size:,} bytes")
        print(f"ZIP64 Central Directory offset: {cd_offset}")
        
        return int(cd_offset), int(cd_size)

    # Also update your _find_end_of_central_directory method:

    def _find_end_of_central_directory(self) -> Tuple[int, int]:
        """Find the End of Central Directory record - UPDATED VERSION"""
        eocd_signature = b'\x50\x4b\x05\x06'
        search_size = min(4096, self.file_size)
        start_pos = self.file_size - search_size
        data = self._read_bytes(start_pos, search_size)
        
        for i in range(len(data) - 22, -1, -1):
            if data[i:i+4] == eocd_signature:
                eocd_offset = start_pos + i
                eocd_data = data[i:i+22]
                
                signature, disk_num, cd_disk, cd_entries_disk, cd_entries_total, \
                cd_size, cd_offset, comment_len = struct.unpack('<LHHHHLLH', eocd_data)
                
                print(f"Found EOCD at offset: {eocd_offset}")
                print(f"Central Directory entries: {cd_entries_total}")
                print(f"Central Directory size: {cd_size:,} bytes")
                print(f"Central Directory offset: {cd_offset}")
                
                # Check if this is a ZIP64 file
                if cd_offset == 0xFFFFFFFF or cd_entries_total == 0xFFFF or cd_size == 0xFFFFFFFF:
                    print("ZIP64 format detected - searching for ZIP64 records...")
                    return self._find_zip64_end_of_central_directory(eocd_offset)
                
                return cd_offset, cd_size
        
        raise Exception("End of Central Directory not found")
    
    # Fix for ZIP64 extra field parsing in your _parse_central_directory method
    # Replace the existing method with this enhanced version:

    def _parse_central_directory(self, cd_offset: int, cd_size: int):
        """Parse the central directory to get file information - ZIP64 EXTRA FIELD FIX"""
        print(f"\nReading Central Directory...")
        cd_data = self._read_bytes(cd_offset, cd_size)
        print(f"Read {len(cd_data)} bytes of central directory data")
        
        offset = 0
        file_count = 0
        entry_count = 0
        
        while offset < len(cd_data) - 4:
            # Check for Central Directory File Header signature: 0x02014b50
            if offset + 4 > len(cd_data):
                break
                
            signature = struct.unpack('<L', cd_data[offset:offset+4])[0]
            
            if signature != 0x02014b50:
                print(f"Unexpected signature at offset {offset}: 0x{signature:08x}")
                break
            
            # Make sure we have enough data for the fixed header
            if offset + 46 > len(cd_data):
                print("Not enough data for complete central directory header")
                break
            
            try:
                # Parse central directory file header (46 bytes fixed part)
                header_data = cd_data[offset:offset+46]
                header = struct.unpack('<LHHHHHHLLLHHHHHLL', header_data)
                
                compressed_size = header[8]
                uncompressed_size = header[9]
                filename_len = header[10]
                extra_len = header[11] 
                comment_len = header[12]
                local_header_offset = header[16]
                compression_method = header[4]
                
                entry_count += 1
                
                # Extract filename
                filename_start = offset + 46
                filename_end = filename_start + filename_len
                
                if filename_end > len(cd_data):
                    print(f"ERROR: Filename extends beyond CD data")
                    break
                    
                if filename_len > 0:
                    filename_bytes = cd_data[filename_start:filename_end]
                    filename = filename_bytes.decode('utf-8', errors='ignore')
                else:
                    filename = f"unnamed_file_{entry_count}"
                    print(f"  No filename, using: '{filename}'")
                
                # ===== ZIP64 EXTRA FIELD PARSING =====
                # Parse extra field to get real ZIP64 sizes if needed
                real_compressed_size = compressed_size
                real_uncompressed_size = uncompressed_size
                real_local_header_offset = local_header_offset
                
                if extra_len > 0:
                    extra_start = filename_end
                    extra_end = extra_start + extra_len
                    
                    if extra_end <= len(cd_data):
                        extra_data = cd_data[extra_start:extra_end]
                        
                        # Parse ZIP64 extended information if present
                        zip64_info = self._parse_zip64_extra_field(
                            extra_data, 
                            compressed_size, 
                            uncompressed_size, 
                            local_header_offset
                        )
                        if zip64_info:
                            real_uncompressed_size, real_compressed_size, real_local_header_offset = zip64_info
                            # Show ZIP64 debug info only for files where sizes actually changed
                            if (real_compressed_size != compressed_size or real_uncompressed_size != uncompressed_size):
                                print(f"ZIP64 info for {filename}:")
                                print(f"  Original sizes: compressed={compressed_size}, uncompressed={uncompressed_size}")
                                print(f"  ZIP64 sizes: compressed={real_compressed_size}, uncompressed={real_uncompressed_size}")
                
                # Skip directories and empty entries
                if not filename.endswith('/') and (real_compressed_size > 0 or real_uncompressed_size > 0):
                    self.files_info[file_count] = {
                        'filename': filename,
                        'compressed_size': real_compressed_size,
                        'uncompressed_size': real_uncompressed_size,
                        'compression_method': compression_method,
                        'local_header_offset': real_local_header_offset
                    }
                    file_count += 1
                
                # Move to next entry
                offset = offset + 46 + filename_len + extra_len + comment_len
                
            except Exception as e:
                print(f"Error parsing entry {entry_count} at offset {offset}: {e}")
                # Try to skip this entry and continue
                offset += 46
                continue
        
        print(f"\nProcessed {entry_count} entries, found {file_count} valid files")

    def _parse_zip64_extra_field(self, extra_data: bytes, compressed_size: int, uncompressed_size: int, local_header_offset: int):
        """Parse ZIP64 extended information from extra field"""
        
        offset = 0
        while offset + 4 <= len(extra_data):
            # Read extra field header
            header_id = struct.unpack('<H', extra_data[offset:offset+2])[0]
            data_size = struct.unpack('<H', extra_data[offset+2:offset+4])[0]
            
            # Check if this is ZIP64 extended information (header ID = 0x0001)
            if header_id == 0x0001:
                zip64_data = extra_data[offset+4:offset+4+data_size]
                
                # Parse ZIP64 data based on what fields are 0xFFFFFFFF in the main header
                zip64_offset = 0
                real_uncompressed_size = uncompressed_size
                real_compressed_size = compressed_size
                real_local_header_offset = local_header_offset
                
                # If uncompressed size is 0xFFFFFFFF, read 8-byte ZIP64 value
                if uncompressed_size == 0xFFFFFFFF and zip64_offset + 8 <= len(zip64_data):
                    real_uncompressed_size = struct.unpack('<Q', zip64_data[zip64_offset:zip64_offset+8])[0]
                    zip64_offset += 8
                
                # If compressed size is 0xFFFFFFFF, read 8-byte ZIP64 value
                if compressed_size == 0xFFFFFFFF and zip64_offset + 8 <= len(zip64_data):
                    real_compressed_size = struct.unpack('<Q', zip64_data[zip64_offset:zip64_offset+8])[0]
                    zip64_offset += 8
                
                # If local header offset is 0xFFFFFFFF, read 8-byte ZIP64 value
                if local_header_offset == 0xFFFFFFFF and zip64_offset + 8 <= len(zip64_data):
                    real_local_header_offset = struct.unpack('<Q', zip64_data[zip64_offset:zip64_offset+8])[0]
                    zip64_offset += 8
                
                return (real_uncompressed_size, real_compressed_size, real_local_header_offset)
            
            # Move to next extra field
            offset += 4 + data_size
        
        return None  # No ZIP64 info found

    def list_files(self):
        """List all files in the remote ZIP"""
        try:
            cd_offset, cd_size = self._find_end_of_central_directory()
            self._parse_central_directory(cd_offset, cd_size)
            
            if not self.files_info:
                print("No files found in the ZIP archive")
                return
            
            print(f"\n{'No.':<4} {'Filename':<50} {'Size (MB)':<12} {'Compressed (MB)':<15} {'Compression':<12}")
            print("-" * 95)
            
            for idx, file_info in self.files_info.items():
                filename = file_info['filename']
                size_mb = file_info['uncompressed_size'] / (1024 * 1024)
                compressed_mb = file_info['compressed_size'] / (1024 * 1024)
                compression = file_info['compression_method']
                
                # Compression method names
                compression_names = {0: 'None', 8: 'Deflate', 9: 'Deflate64', 12: 'BZip2', 14: 'LZMA'}
                compression_str = compression_names.get(compression, f'Method {compression}')
                
                display_name = filename if len(filename) <= 48 else filename[:45] + "..."
                print(f"{idx+1:<4} {display_name:<50} {size_mb:<12.2f} {compressed_mb:<15.2f} {compression_str:<12}")
                
        except Exception as e:
            print(f"Error listing files: {e}")
            traceback.print_exc()
    
    # Also update your _get_local_file_header_info method to handle ZIP64:

    def _get_local_file_header_info(self, local_header_offset: int) -> Tuple[int, int, int]:
        """Get the actual data offset by parsing local file header - ZIP64 ENHANCED"""
        header_data = self._read_bytes(local_header_offset, 30)
        
        signature = struct.unpack('<L', header_data[0:4])[0]
        if signature != 0x04034b50:
            raise Exception("Invalid local file header signature")
        
        # Parse local file header
        sig, ver, flags, compression_method, mtime, mdate, crc32, compressed_size, uncompressed_size, filename_len, extra_len = struct.unpack('<LHHHHHLLLHH', header_data)
        
        # Read filename and extra field
        variable_data = self._read_bytes(local_header_offset + 30, filename_len + extra_len)
        
        # Parse extra field for ZIP64 information if needed
        real_compressed_size = compressed_size
        if extra_len > 0 and (compressed_size == 0xFFFFFFFF or uncompressed_size == 0xFFFFFFFF):
            extra_data = variable_data[filename_len:filename_len + extra_len]
            
            # Parse ZIP64 extended information in local header
            offset = 0
            while offset + 4 <= len(extra_data):
                header_id = struct.unpack('<H', extra_data[offset:offset+2])[0]
                data_size = struct.unpack('<H', extra_data[offset+2:offset+4])[0]
                
                if header_id == 0x0001:  # ZIP64 extended information
                    zip64_data = extra_data[offset+4:offset+4+data_size]
                    zip64_offset = 0
                    
                    # If uncompressed size is 0xFFFFFFFF, read 8-byte ZIP64 value
                    if uncompressed_size == 0xFFFFFFFF and zip64_offset + 8 <= len(zip64_data):
                        zip64_offset += 8  # Skip uncompressed size
                    
                    # If compressed size is 0xFFFFFFFF, read 8-byte ZIP64 value
                    if compressed_size == 0xFFFFFFFF and zip64_offset + 8 <= len(zip64_data):
                        real_compressed_size = struct.unpack('<Q', zip64_data[zip64_offset:zip64_offset+8])[0]
                        break
                
                offset += 4 + data_size
        
        data_offset = local_header_offset + 30 + filename_len + extra_len
        return data_offset, real_compressed_size, compression_method

    # And also enhance your download_file method for better large file handling:

    def download_file(self, file_number: int, output_dir: str = None, max_workers: int = 4):  # Reduced workers for large files
        """Download a specific file by its number - ENHANCED FOR LARGE FILES"""
        try:
            if file_number < 1 or file_number > len(self.files_info):
                print(f"Invalid file number. Please choose between 1 and {len(self.files_info)}")
                return False
            
            file_info = self.files_info[file_number - 1]
            filename = file_info['filename']
            local_header_offset = file_info['local_header_offset']
            expected_size = file_info['compressed_size']
            uncompressed_size = file_info['uncompressed_size']
            
            print(f"\nDownloading: {filename}")
            print(f"Compressed size: {expected_size:,} bytes ({expected_size / (1024*1024):.2f} MB)")
            print(f"Uncompressed size: {uncompressed_size:,} bytes ({uncompressed_size / (1024*1024):.2f} MB)")
            
            data_offset, actual_compressed_size, compression_method = self._get_local_file_header_info(local_header_offset)
            
            # Use the larger of the two sizes (in case ZIP64 info wasn't parsed correctly)
            if actual_compressed_size > expected_size:
                print(f"Using ZIP64 size from local header: {actual_compressed_size:,} bytes")
                expected_size = actual_compressed_size

            # Determine base folder from ZIP URL if output_dir not specified
            if output_dir is None:
                filename_from_server = get_filename_from_headers(self.original_url)
                base_name = os.path.splitext(filename_from_server)[0]
                output_dir = os.path.join(os.getcwd(), base_name)
            os.makedirs(output_dir, exist_ok=True)

            # Recreate internal folder structure, sanitize each part
            internal_path = file_info['filename']
            parts = internal_path.split('/')
            safe_parts = []
            for p in parts:
                if not isinstance(p, str):
                    p = str(p)
                safe = p.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_') \
                        .replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
                safe_parts.append(safe)

            output_path = os.path.join(output_dir, *safe_parts)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # Adjust chunk size based on file size (larger files = larger chunks)
            if expected_size > 1024 * 1024 * 1024:  # > 1GB
                chunk_size = 16 * 1024 * 1024  # 16MB chunks for large files
                max_workers = 2  # Fewer workers for very large files
            elif expected_size > 100 * 1024 * 1024:  # > 100MB
                chunk_size = 8 * 1024 * 1024   # 8MB chunks
                max_workers = 3
            else:
                chunk_size = 4 * 1024 * 1024   # 4MB chunks for smaller files
            
            total_chunks = (expected_size + chunk_size - 1) // chunk_size
            downloaded_chunks = [None] * total_chunks
            downloaded_bytes_lock = threading.Lock()
            downloaded_bytes = 0

            print(f"Using {total_chunks} chunks of {chunk_size/(1024*1024):.1f}MB each with {max_workers} workers")

            def fetch_chunk(idx):
                nonlocal downloaded_bytes
                start = data_offset + idx * chunk_size
                end = min(start + chunk_size - 1, data_offset + expected_size - 1)
                chunk = self._read_bytes(start, end - start + 1)
                with downloaded_bytes_lock:
                    downloaded_bytes += len(chunk)
                    progress = (downloaded_bytes / expected_size) * 100
                    print(f"\rProgress: {progress:.2f}% ({downloaded_bytes:,} / {expected_size:,} bytes)", end='', flush=True)
                return idx, chunk

            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(fetch_chunk, i) for i in range(total_chunks)]
                for future in concurrent.futures.as_completed(futures):
                    idx, chunk_data = future.result()
                    downloaded_chunks[idx] = chunk_data

            print()  # Newline after progress

            compressed_data = b''.join(downloaded_chunks)
            
            # Handle decompression
            if compression_method == 8:
                print("Decompressing DEFLATE data...")
                try:
                    decompressed_data = zlib.decompress(compressed_data, -15)
                    with open(output_path, 'wb') as f:
                        f.write(decompressed_data)
                    print(f"Decompressed {len(compressed_data):,} bytes to {len(decompressed_data):,} bytes")
                except Exception as e:
                    print(f"Decompression failed: {e}")
                    print("Saving compressed data instead...")
                    with open(output_path, 'wb') as f:
                        f.write(compressed_data)
            else:
                with open(output_path, 'wb') as f:
                    f.write(compressed_data)

            print(f"\nDownloaded successfully: {output_path}")

            if compression_method == 0:
                print("File stored without compression")
            elif compression_method == 8:
                print("File uses DEFLATE compression - decompressed on save")
            else:
                print(f"File uses compression method {compression_method}")

            return True

        except Exception as e:
            print(f"\nError downloading file: {e}")
            import traceback
            traceback.print_exc()
            return False


def main():
    """Main interactive function"""
    print("="*70)
    print("        Fixed Remote ZIP File Reader & Downloader")
    print("="*70)
    #print("This version fixes central directory parsing issues")
    #print("Works with GitHub release URLs")
    #print("Better ZIP structure parsing")
    #print("Detailed debugging information")
    print()
    
    url = input("Enter the ZIP file URL: ").strip()
    
    if not url:
        print("No URL provided. Exiting.")
        return
    
    if not (url.startswith('http://') or url.startswith('https://')):
        print("Please provide a valid HTTP/HTTPS URL.")
        return
    
    try:
        zip_reader = FixedRemoteZipReader(url)
        
        print("\nScanning ZIP file contents...")
        zip_reader.list_files()
        
        if not zip_reader.files_info:
            print("No files found to download.")
            return
        
        # Interactive download loop
        while True:
            print(f"\n{'='*70}")
            print("Download Options:")
            print("- Enter a file number (1-{}) to download".format(len(zip_reader.files_info)))
            print("- Enter 'list' to show files again")
            print("- Enter 'quit' to exit")
            
            choice = input("\nYour choice: ").strip().lower()
            
            if choice == 'quit':
                print("Goodbye!")
                break
            elif choice == 'list':
                zip_reader.list_files()
                continue
            
            try:
                file_num = int(choice)
                if 1 <= file_num <= len(zip_reader.files_info):
                    success = zip_reader.download_file(file_num)
                    if success:
                        print("\nFile downloaded successfully!")
                        
                        another = input("\nDownload another file? (y/n): ").strip().lower()
                        if another != 'y':
                            print("Goodbye!")
                            break
                    else:
                        print("Download failed. Please try again.")
                else:
                    print(f"Invalid file number. Please choose between 1 and {len(zip_reader.files_info)}")
            except ValueError:
                print("Invalid input. Please enter a number, 'list', or 'quit'.")
                
    except Exception as e:
        print(f"Error: {e}")
        if "501" not in str(e):
            print("Please check your URL and internet connection.")


if __name__ == "__main__":
    main()