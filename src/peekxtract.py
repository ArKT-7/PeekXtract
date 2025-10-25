#
# Copyright (C) 2025-26 https://github.com/ArKT-7/PeekXtract
#

__version__ = "2.0.3"

import requests
import struct
import zlib
import bz2
from typing import Tuple, List, Optional
import os
import concurrent.futures
import threading
from urllib.parse import urlparse, unquote
import ntpath
import traceback
import binascii
import re
import time
import lzma
import sys

def format_size(bytes_size: int) -> str:
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.2f} KB"
    elif bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.2f} MB"
    else:
        return f"{bytes_size / (1024 * 1024 * 1024):.2f} GB"

def format_time(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds // 3600)
        remaining = seconds % 3600
        minutes = int(remaining // 60)
        secs = int(remaining % 60)
        return f"{hours}h {minutes}m {secs}s"

def get_compression_name(method: int) -> str:
    compression_methods = {
        0: "STORED (No Compression)",
        1: "SHRUNK",
        2: "REDUCED (factor 1)",
        3: "REDUCED (factor 2)",
        4: "REDUCED (factor 3)",
        5: "REDUCED (factor 4)",
        6: "IMPLODED",
        7: "DEFLATED",
        8: "DEFLATE",
        9: "DEFLATE64",
        10: "PKWARE IMPLODE",
        12: "BZIP2",
        14: "LZMA",
        18: "BZIP2 (alternate)",
        19: "XZ",
        20: "LZMA (old)",
        97: "WinZip AES Encrypted",
    }
    return compression_methods.get(method, f"Unknown Method {method}")

def decompress_data(data: bytes, compression_method: int, uncompressed_size: int) -> Optional[bytes]:

    try:
        if compression_method == 0:
            # STORED - no compression
            return data
        
        elif compression_method == 8:
            # DEFLATE
            print(f"Decompressing DEFLATE data ({format_size(len(data))} → {format_size(uncompressed_size)})...")
            return zlib.decompress(data, -15)
        
        elif compression_method == 12:
            # BZIP2
            print(f"Decompressing BZIP2 data ({format_size(len(data))} → {format_size(uncompressed_size)})...")
            return bz2.decompress(data)
        
        elif compression_method == 14:
            # LZMA
            print(f"Decompressing LZMA data ({format_size(len(data))} → {format_size(uncompressed_size)})...")
            return lzma.decompress(data)
        
        else:
            print(f"Warning: Compression method {compression_method} ({get_compression_name(compression_method)}) not supported")
            print("Saving compressed data instead...")
            return None
    
    except Exception as e:
        print(f"Decompression failed: {e}")
        return None

def resolve_github_download_url(url: str) -> str:
    """Try to resolve GitHub release URLs to their actual download URLs"""
    if 'github.com' in url and '/releases/download/' in url:
        print("\nDetected GitHub release URL - attempting to resolve actual download URL...")
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        try:
            response = session.head(url, allow_redirects=True, timeout=10)
            resolved_url = response.url
            print(f"Resolved GitHub URL:")
            print(f"  Original: {url}")
            print(f"  Resolved: {resolved_url}")
            test_response = session.get(resolved_url, headers={'Range': 'bytes=0-0'}, timeout=10)
            if test_response.status_code == 206:
                print("Resolved URL supports range requests!")
                return resolved_url
            else:
                print(f"Resolved URL doesn't support range requests (status: {test_response.status_code})")
                return url
        except requests.exceptions.Timeout:
            print("Error: Connection timed out while resolving GitHub URL.")
            return url
        except requests.exceptions.ConnectionError:
            print("Error: Failed to connect while resolving GitHub URL.")
            return url
        except Exception as e:
            print(f"Failed to resolve GitHub URL: {e}")
            return url
    return url

def resolve_onedrive_url(url: str) -> str:
    """Try to resolve OneDrive share URLs to direct download URLs"""
    if not ('1drv.ms' in url.lower() or 'onedrive.live.com' in url.lower()):
        return url

    # Check if selenium is available
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        print("\nOneDrive link detected but selenium not installed.")
        print("For OneDrive support: pip install selenium")
        return url

    print("\nDetected OneDrive URL - extracting direct download link...")

    driver = None
    try:
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--disable-extensions')
        chrome_options.add_argument('--disable-plugins')
        chrome_options.add_argument('--no-default-browser-check')
        chrome_options.add_argument('--no-first-run')
        chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(30)
        driver.get(url)

        button_selectors = [
            "button[aria-label*='Download']",
            "button[title*='Download']",
            "[data-icon-name='Download']",
        ]

        button = None
        for selector in button_selectors:
            start_time = time.time()
            while time.time() - start_time < 30:
                try:
                    button = driver.find_element(By.CSS_SELECTOR, selector)
                    if button.is_displayed() and button.is_enabled():
                        break
                except:
                    pass
                time.sleep(1)
            if button and button.is_displayed():
                break

        if button:
            driver.execute_cdp_cmd('Network.enable', {})
            driver.execute_script("arguments[0].click();", button)
            time.sleep(2)

        logs = driver.get_log('performance')

        for log_entry in logs:
            try:
                import json
                message = json.loads(log_entry['message'])
                params = message.get('message', {}).get('params', {})

                if 'request' in params:
                    direct_url = params['request'].get('url', '')
                    if 'download.aspx' in direct_url and 'tempauth=' in direct_url:
                        print("OneDrive direct URL extracted successfully!")
                        print(f"Resolved OneDrive URL:")
                        print(f"  {direct_url}")
                        return direct_url

                if 'redirectResponse' in params:
                    direct_url = params['redirectResponse'].get('url', '')
                    if 'download.aspx' in direct_url and 'tempauth=' in direct_url:
                        print("OneDrive direct URL extracted successfully!")
                        print(f"Resolved OneDrive URL:")
                        print(f"  {direct_url}")
                        return direct_url
            except:
                continue

        current_url = driver.current_url
        if 'download.aspx' in current_url:
            print("OneDrive direct URL extracted from redirect!")
            print(f"Resolved OneDrive URL:")
            print(f"  {current_url}")
            return current_url

        print("Could not extract OneDrive direct URL, using original...")
        return url

    except Exception as e:
        print(f"OneDrive resolution error: {e}")
        print("Using original URL...")
        return url

    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass


def get_filename_from_headers(url: str) -> str:
    """Get the actual filename from HTTP headers"""
    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0'
        })
        r = session.head(url, allow_redirects=True, timeout=10)
        cd = r.headers.get('content-disposition')
        if cd:
            m = re.search(r'filename\*?=(?:UTF-8\'\')?\"?([^\";]+)\"?', cd, re.IGNORECASE)
            if m:
                raw_name = m.group(1)
                decoded_name = unquote(raw_name)
                if decoded_name.lower().startswith("utf-8''"):
                    decoded_name = decoded_name[7:]
                return decoded_name
        final = urlparse(r.url).path
        name = ntpath.basename(final)
        if name:
            return name
    except Exception:
        pass
    p = urlparse(url).path
    return ntpath.basename(p) or "ArKT-Magic"

def parse_range(range_str: str, max_val: int) -> List[int]:
    """Parse range string like '1-5,7,10-12' into list of numbers"""
    result = []
    parts = range_str.split(',')
    for part in parts:
        part = part.strip()
        if '-' in part:
            start, end = part.split('-', 1)
            try:
                start_num = int(start.strip())
                end_num = int(end.strip())
                if start_num <= end_num and start_num >= 1 and end_num <= max_val:
                    result.extend(range(start_num, end_num + 1))
            except ValueError:
                continue
        else:
            try:
                num = int(part)
                if 1 <= num <= max_val:
                    result.append(num)
            except ValueError:
                continue
    return sorted(set(result))

class EnhancedRemoteZipReader:
    def __init__(self, url: str):
        self.original_url = url
        # Try OneDrive resolution first, then GitHub
        temp_url = resolve_onedrive_url(url)
        self.url = resolve_github_download_url(temp_url)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        # Early URL validation - fail fast on timeout or connection error
        if not self._validate_url():
            raise Exception("URL validation failed, please check your network or the URL and try again.")
        self.file_size = 0
        self.files_info = {}
        self.actual_url = None
        self.current_display_mapping = {}
        self._initialize()

    def _validate_url(self, timeout=30) -> bool:
        try:
            print("\nChecking URL connectivity...")
            response = self.session.head(self.url, allow_redirects=True, timeout=timeout)
            if response.status_code >= 400:
                print(f"Error: Server returned status code {response.status_code}")
                return False
            test_resp = self.session.get(self.url, headers={'Range': 'bytes=0-0'}, timeout=timeout)
            if test_resp.status_code == 501:
                print("Error: Server does not support HTTP Range requests (501).")
                return False
            if test_resp.status_code not in [200, 206]:
                print(f"Warning: Range request returned status {test_resp.status_code}")
            return True
        except requests.exceptions.Timeout as e:
            print(f"Error: Connection timed out. {e}")
            return False
        except requests.exceptions.ConnectionError as e:
            print(f"Error: Failed to establish connection. {e}")
            return False
        except Exception as e:
            print(f"Unexpected error during URL validation: {e}")
            return False

    def _initialize(self):
        """Initialize by getting file size and checking range support"""
        try:
            print("\nInitializing PeekXtract ZIP reader...")
            response = self.session.head(self.url, allow_redirects=True)
            self.actual_url = response.url
            if self.actual_url != self.url:
                print(f"Following redirect to: {self.actual_url}")
            test_response = self.session.get(self.actual_url, headers={'Range': 'bytes=0-0'})
            if test_response.status_code == 501:
                print("\n" + "="*70)
                print("ERROR: SERVER DOESN'T SUPPORT RANGE REQUESTS")
                print("="*70)
                #print("The server returned 501 Not Implemented for range requests.")
                #print("This means we cannot read the ZIP file remotely.")
                raise Exception("Server doesn't support HTTP range requests (501)")
            elif test_response.status_code != 206:
                print(f"Warning: Range request returned status {test_response.status_code}")
            if 'content-length' in response.headers:
                self.file_size = int(response.headers['content-length'])
                print(f"Remote file size: {self.file_size:,} bytes ({format_size(self.file_size)})")
            elif test_response.status_code == 206 and 'content-range' in test_response.headers:
                content_range = test_response.headers['content-range']
                self.file_size = int(content_range.split('/')[-1])
                print(f"Remote file size: {self.file_size:,} bytes ({format_size(self.file_size)})")
            else:
                raise Exception("Cannot determine file size")
        except Exception as e:
            if "501" in str(e):
                raise e
            raise Exception(f"Failed to initialize remote file: {e}")

    def _read_bytes(self, start: int, length: int, max_retries: int = 3) -> bytes:
        """Read specific byte range from remote file with retry logic"""
        end = start + length - 1
        headers = {'Range': f'bytes={start}-{end}'}
        
        for attempt in range(max_retries):
            try:
                response = self.session.get(self.actual_url, headers=headers, timeout=30)
                if response.status_code not in [206, 200]:
                    raise Exception(f"Range request failed with status {response.status_code}")
                return response.content
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"\nRetry {attempt + 1}/{max_retries} for bytes {start}-{end} (waiting {wait_time}s)...")
                    time.sleep(wait_time)
                    continue
                raise Exception(f"Failed to read bytes {start}-{end} after {max_retries} attempts: {e}")

    def _find_zip64_end_of_central_directory(self, eocd_offset: int) -> Tuple[int, int]:
        """Find ZIP64 End of Central Directory record"""
        zip64_locator_offset = eocd_offset - 20
        #print(f"Looking for ZIP64 locator at offset: {zip64_locator_offset}")
        if zip64_locator_offset < 0:
            raise Exception("ZIP64 locator would be at negative offset")
        try:
            locator_data = self._read_bytes(zip64_locator_offset, 20)
        except Exception as e:
            raise Exception(f"Failed to read ZIP64 locator: {e}")
        signature = struct.unpack('<I', locator_data[0:4])[0]
        if signature != 0x07064b50:
            #print(f"ZIP64 locator signature not found at expected position (got 0x{signature:08x})")
            return self._fallback_find_zip64_locator()
        #print("Found ZIP64 locator signature!")
        disk_num, zip64_eocd_offset, total_disks = struct.unpack('<IQI', locator_data[4:20])
        #print(f"ZIP64 EOCD offset: {zip64_eocd_offset}")
        #print(f"Disk number: {disk_num}")
        #print(f"Total disks: {total_disks}")
        return zip64_eocd_offset, total_disks

    def _fallback_find_zip64_locator(self) -> Tuple[int, int]:
        """Fallback method to search for ZIP64 locator"""
        print("Searching for ZIP64 locator using fallback method...")
        zip64_locator_sig = b'\x50\x4b\x06\x07'
        search_size = min(8192, self.file_size)
        start_pos = max(0, self.file_size - search_size)
        data = self._read_bytes(start_pos, search_size)
        zip64_locator_pos = None
        for i in range(len(data) - 20, -1, -1):
            if data[i:i+4] == zip64_locator_sig:
                zip64_locator_pos = start_pos + i
                #print(f"Found ZIP64 locator at offset: {zip64_locator_pos}")
                break
        if zip64_locator_pos is None:
            raise Exception("ZIP64 End of Central Directory Locator not found")
        locator_data = self._read_bytes(zip64_locator_pos, 20)
        signature, disk_num, zip64_eocd_offset, total_disks = struct.unpack('<IIQI', locator_data)
        return zip64_eocd_offset, total_disks

    def _find_end_of_central_directory(self) -> Tuple[int, int]:
        """Find the End of Central Directory record"""
        eocd_signature = b'\x50\x4b\x05\x06'
        search_size = min(4096, self.file_size)
        start_pos = self.file_size - search_size
        data = self._read_bytes(start_pos, search_size)
        for i in range(len(data) - 22, -1, -1):
            if data[i:i+4] == eocd_signature:
                eocd_offset = start_pos + i
                eocd_data = data[i:i+22]
                signature, disk_num, cd_disk, cd_entries_disk, cd_entries_total, \
                    cd_size, cd_offset, comment_len = struct.unpack('<IHHHHIIH', eocd_data)
                #print(f"Found EOCD at offset: {eocd_offset}")
                #print(f"Central Directory entries: {cd_entries_total}")
                #print(f"Central Directory size: {cd_size:,} bytes")
                #print(f"Central Directory offset: {cd_offset}")
                if cd_offset == 0xFFFFFFFF or cd_entries_total == 0xFFFF:
                    #print("ZIP64 format detected - searching for ZIP64 records...")
                    zip64_eocd_offset, total_disks = self._find_zip64_end_of_central_directory(eocd_offset)
                    zip64_eocd_data = self._read_bytes(zip64_eocd_offset, 56)
                    zip64_sig, zip64_size, version_made, version_needed, disk_num, cd_disk, \
                        cd_entries_disk, cd_entries_total, cd_size, cd_offset = struct.unpack('<IQHHIIQQQQ', zip64_eocd_data[:56])
                    #print(f"ZIP64 EOCD size: {zip64_size}")
                    #print(f"ZIP64 Central Directory entries: {cd_entries_total}")
                    #print(f"ZIP64 Central Directory size: {cd_size:,} bytes")
                    #print(f"ZIP64 Central Directory offset: {cd_offset}")
                return cd_offset, cd_size
        raise Exception("End of Central Directory not found")

    def _parse_central_directory(self):
        """Parse the central directory to get file information"""
        cd_offset, cd_size = self._find_end_of_central_directory()
        print(f"\nReading Central Directory...")
        cd_data = self._read_bytes(cd_offset, cd_size)
        #print(f"Read {len(cd_data)} bytes of central directory data")
        offset = 0
        entry_count = 0
        file_count = 0
        while offset < len(cd_data):
            entry_count += 1
            if offset + 46 > len(cd_data):
                break
            try:
                header_data = cd_data[offset:offset+46]
                header = struct.unpack('<IHHHHHHIIIHHHHHII', header_data)
                signature = header[0]
                if signature != 0x02014b50:
                    break
                version_made, version_needed, flags, compression_method, mod_time, mod_date, \
                    crc32, compressed_size, uncompressed_size, filename_len, extra_len, \
                    comment_len, disk_start, internal_attr, external_attr, local_header_offset = header[1:]
                filename_start = offset + 46
                filename_end = filename_start + filename_len
                if filename_end > len(cd_data):
                    break
                if filename_len > 0:
                    filename_bytes = cd_data[filename_start:filename_end]
                    filename = filename_bytes.decode('utf-8', errors='ignore')
                else:
                    filename = f"unnamed_file_{entry_count}"
                real_compressed_size = compressed_size
                real_uncompressed_size = uncompressed_size
                real_local_header_offset = local_header_offset
                if extra_len > 0:
                    extra_start = filename_end
                    extra_end = extra_start + extra_len
                    if extra_end <= len(cd_data):
                        extra_data = cd_data[extra_start:extra_end]
                        zip64_info = self._parse_zip64_extra_field(
                            extra_data,
                            compressed_size,
                            uncompressed_size,
                            local_header_offset
                        )
                        if zip64_info:
                            real_uncompressed_size, real_compressed_size, real_local_header_offset = zip64_info
                if not filename.endswith('/') and (real_compressed_size > 0 or real_uncompressed_size > 0):
                    self.files_info[file_count] = {
                        'filename': filename,
                        'compressed_size': real_compressed_size,
                        'uncompressed_size': real_uncompressed_size,
                        'compression_method': compression_method,
                        'local_header_offset': real_local_header_offset,
                        'crc32': crc32
                    }
                    file_count += 1
                offset = offset + 46 + filename_len + extra_len + comment_len
            except Exception as e:
                #print(f"Error parsing entry {entry_count} at offset {offset}: {e}")
                offset += 46
                continue
        print(f"\nProcessed {entry_count} entries, found {file_count} valid files")

    def _parse_zip64_extra_field(self, extra_data: bytes, compressed_size: int, uncompressed_size: int, local_header_offset: int):
        """Parse ZIP64 extended information from extra field"""
        offset = 0
        while offset + 4 <= len(extra_data):
            header_id = struct.unpack('<H', extra_data[offset:offset+2])[0]
            data_size = struct.unpack('<H', extra_data[offset+2:offset+4])[0]
            if header_id == 0x0001:
                field_offset = 0
                result_uncompressed = uncompressed_size
                result_compressed = compressed_size
                result_offset = local_header_offset
                if uncompressed_size == 0xFFFFFFFF and field_offset + 8 <= data_size:
                    result_uncompressed = struct.unpack('<Q', extra_data[offset+4+field_offset:offset+4+field_offset+8])[0]
                    field_offset += 8
                if compressed_size == 0xFFFFFFFF and field_offset + 8 <= data_size:
                    result_compressed = struct.unpack('<Q', extra_data[offset+4+field_offset:offset+4+field_offset+8])[0]
                    field_offset += 8
                if local_header_offset == 0xFFFFFFFF and field_offset + 8 <= data_size:
                    result_offset = struct.unpack('<Q', extra_data[offset+4+field_offset:offset+4+field_offset+8])[0]
                return result_uncompressed, result_compressed, result_offset
            offset += 4 + data_size
        return None

    def list_files(self, filter_pattern: str = None):
        """List all files in the ZIP archive with optional filtering"""
        if not self.files_info:
            self._parse_central_directory()
        
        self.current_display_mapping = {}
        files_to_display = {}
        
        if filter_pattern:
            try:
                pattern = re.compile(filter_pattern, re.IGNORECASE)
                for idx, info in self.files_info.items():
                    if pattern.search(info['filename']):
                        display_idx = len(files_to_display)
                        files_to_display[display_idx] = info
                        self.current_display_mapping[display_idx] = idx
            except re.error:
                filter_lower = filter_pattern.lower()
                for idx, info in self.files_info.items():
                    if filter_lower in info['filename'].lower():
                        display_idx = len(files_to_display)
                        files_to_display[display_idx] = info
                        self.current_display_mapping[display_idx] = idx
            
            if not files_to_display:
                print(f"\nNo files match filter: '{filter_pattern}'")
                print("Showing all files instead...\n")
                files_to_display = self.files_info
                self.current_display_mapping = {i: i for i in self.files_info.keys()}
        else:
            files_to_display = self.files_info
            self.current_display_mapping = {i: i for i in self.files_info.keys()}
        
        print(f"\nNo.  {'Filename':<50} {'Size':<15} {'Compressed':<15} Compression")
        print("-" * 100)
        
        for display_idx, info in files_to_display.items():
            filename = info['filename']
            if len(filename) > 50:
                filename = filename[:47] + "..."
            
            size_str = format_size(info['uncompressed_size'])
            compressed_str = format_size(info['compressed_size'])
            
            comp_name = get_compression_name(info['compression_method'])
            if len(comp_name) > 20:
                comp_name = comp_name[:17] + "..."
            
            print(f"{display_idx + 1:<4} {filename:<50} {size_str:<15} {compressed_str:<15} {comp_name}")

    def search_files(self, pattern: str):
        """Search for files matching a pattern"""
        if not self.files_info:
            self._parse_central_directory()
        
        matches = []
        try:
            regex = re.compile(pattern, re.IGNORECASE)
            for idx, info in self.files_info.items():
                if regex.search(info['filename']):
                    matches.append(idx)
        except re.error:
            pattern_lower = pattern.lower()
            for idx, info in self.files_info.items():
                if pattern_lower in info['filename'].lower():
                    matches.append(idx)
        
        return matches

    def _get_actual_file_index(self, display_index: int) -> int:
        """Convert display index to actual file index"""
        if display_index in self.current_display_mapping:
            return self.current_display_mapping[display_index]
        return display_index

    def _get_local_file_header_info(self, local_header_offset: int) -> Tuple[int, int, int]:
        """Get the actual data offset by parsing local file header"""
        header_data = self._read_bytes(local_header_offset, 30)
        signature = struct.unpack('<I', header_data[0:4])[0]
        if signature != 0x04034b50:
            raise Exception(f"Invalid local file header signature: 0x{signature:08x}")
        version_needed, flags, compression_method, mod_time, mod_date, crc32, \
            compressed_size, uncompressed_size, filename_len, extra_len = struct.unpack('<HHHHH III HH', header_data[4:30])
        variable_data = self._read_bytes(local_header_offset + 30, filename_len + extra_len)
        data_offset = local_header_offset + 30 + filename_len + extra_len
        if extra_len > 0 and (compressed_size == 0xFFFFFFFF or uncompressed_size == 0xFFFFFFFF):
            extra_data = variable_data[filename_len:filename_len + extra_len]
            offset = 0
            while offset + 4 <= len(extra_data):
                header_id = struct.unpack('<H', extra_data[offset:offset+2])[0]
                data_size = struct.unpack('<H', extra_data[offset+2:offset+4])[0]
                if header_id == 0x0001:
                    field_offset = 0
                    if uncompressed_size == 0xFFFFFFFF and field_offset + 8 <= data_size:
                        uncompressed_size = struct.unpack('<Q', extra_data[offset+4+field_offset:offset+4+field_offset+8])[0]
                        field_offset += 8
                    if compressed_size == 0xFFFFFFFF and field_offset + 8 <= data_size:
                        compressed_size = struct.unpack('<Q', extra_data[offset+4+field_offset:offset+4+field_offset+8])[0]
                    break
                offset += 4 + data_size
        return data_offset, compressed_size, compression_method

    def _verify_crc32(self, data: bytes, expected_crc32: int) -> bool:
        """Verify CRC32 checksum of data"""
        calculated_crc32 = binascii.crc32(data) & 0xFFFFFFFF
        return calculated_crc32 == expected_crc32

    def download_file(self, display_file_number: int, output_dir: str = None, max_workers: int = 4, verify_integrity: bool = True) -> bool:
        """Download a specific file from the ZIP archive"""
        try:
            actual_file_index = self._get_actual_file_index(display_file_number - 1)
            
            if actual_file_index < 0 or actual_file_index >= len(self.files_info):
                print(f"Invalid file number. Please choose between 1 and {len(self.files_info)}")
                return False
            
            file_info = self.files_info[actual_file_index]
            filename = file_info['filename']
            local_header_offset = file_info['local_header_offset']
            expected_size = file_info['compressed_size']
            uncompressed_size = file_info['uncompressed_size']
            expected_crc32 = file_info['crc32']
            compression_method = file_info['compression_method']
            
            print(f"\nDownloading: {filename}")
            print(f"Compression: {get_compression_name(compression_method)}")
            print(f"Compressed size: {expected_size:,} bytes ({format_size(expected_size)})")
            print(f"Uncompressed size: {uncompressed_size:,} bytes ({format_size(uncompressed_size)})")
            if verify_integrity:
                print(f"Expected CRC32: 0x{expected_crc32:08x}")
            
            data_offset, actual_compressed_size, _ = self._get_local_file_header_info(local_header_offset)
            if actual_compressed_size > expected_size:
                print(f"Using ZIP64 size from local header: {actual_compressed_size:,} bytes")
                expected_size = actual_compressed_size
            
            if output_dir is None:
                filename_from_server = get_filename_from_headers(self.original_url)
                base_name = os.path.splitext(filename_from_server)[0]
                output_dir = os.path.join(os.getcwd(), base_name)
            os.makedirs(output_dir, exist_ok=True)
            
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
            
            if expected_size > 1024 * 1024 * 1024:
                chunk_size = 16 * 1024 * 1024
                max_workers = 2
            elif expected_size > 100 * 1024 * 1024:
                chunk_size = 8 * 1024 * 1024
                max_workers = 3
            else:
                chunk_size = 4 * 1024 * 1024
            
            total_chunks = (expected_size + chunk_size - 1) // chunk_size
            downloaded_chunks = [None] * total_chunks
            downloaded_bytes_lock = threading.Lock()
            downloaded_bytes = 0
            start_time = time.time()
            
            print(f"Using {total_chunks} chunks of {chunk_size/(1024*1024):.1f}MB each with {max_workers} workers\n")
            
            def fetch_chunk(idx):
                nonlocal downloaded_bytes
                start = data_offset + idx * chunk_size
                end = min(start + chunk_size - 1, data_offset + expected_size - 1)
                chunk = self._read_bytes(start, end - start + 1)
                with downloaded_bytes_lock:
                    downloaded_bytes += len(chunk)
                    progress = (downloaded_bytes / expected_size) * 100
                    elapsed = time.time() - start_time
                    speed = downloaded_bytes / elapsed / (1024 * 1024) if elapsed > 0 else 0
                    eta_seconds = (expected_size - downloaded_bytes) / (downloaded_bytes / elapsed) if elapsed > 0 and downloaded_bytes > 0 else 0
                    eta_str = format_time(eta_seconds)
                    print(f"\rProgress: {progress:.2f}% | Speed: {speed:.2f} MB/s | ETA: {eta_str:<12}", end='', flush=True)
                return idx, chunk
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(fetch_chunk, i) for i in range(total_chunks)]
                for future in concurrent.futures.as_completed(futures):
                    idx, chunk_data = future.result()
                    downloaded_chunks[idx] = chunk_data
            
            print()
            compressed_data = b''.join(downloaded_chunks)
            
            # NEW: Try to decompress based on compression method
            final_data = compressed_data
            if compression_method != 0:
                decompressed = decompress_data(compressed_data, compression_method, uncompressed_size)
                if decompressed is not None:
                    final_data = decompressed
                    print(f"Decompressed {format_size(len(compressed_data))} to {format_size(len(final_data))}")
                else:
                    print("Saving compressed data instead...")
                    verify_integrity = False
            
            if verify_integrity:
                print("Verifying file integrity (CRC32)...")
                if self._verify_crc32(final_data, expected_crc32):
                    print("CRC32 verification PASSED")
                else:
                    calculated = binascii.crc32(final_data) & 0xFFFFFFFF
                    print(f"CRC32 verification FAILED!")
                    print(f" Expected:   0x{expected_crc32:08x}")
                    print(f" Calculated: 0x{calculated:08x}")
                    print("Warning: File may be corrupted!")
            
            with open(output_path, 'wb') as f:
                f.write(final_data)
            
            elapsed_total = time.time() - start_time
            print(f"\nDownloaded successfully: {output_path}")
            print(f"Total time: {format_time(elapsed_total)} | Average speed: {expected_size / elapsed_total / (1024 * 1024):.2f} MB/s")
            
            return True
        except Exception as e:
            print(f"\nError downloading file: {e}")
            traceback.print_exc()
            return False

    def download_bulk(self, display_file_numbers: List[int], output_dir: str = None, verify_integrity: bool = True) -> dict:
        """Download multiple files in bulk"""
        actual_file_numbers = [self._get_actual_file_index(num - 1) for num in display_file_numbers]
        
        results = {}
        total_files = len(actual_file_numbers)
        
        print(f"\n{'='*70}")
        print(f"Starting bulk download of {total_files} files...")
        print(f"{'='*70}")
        
        start_time = time.time()
        for idx, actual_file_num in enumerate(actual_file_numbers, 1):
            print(f"\n[{idx}/{total_files}] Processing file #{actual_file_num + 1}")
            success = self.download_file(actual_file_num + 1, output_dir, verify_integrity=verify_integrity)
            results[actual_file_num] = success
        
        elapsed = time.time() - start_time
        
        print(f"\n{'='*70}")
        print("Bulk Download Summary:")
        print(f"{'='*70}")
        successful = sum(1 for v in results.values() if v)
        failed = total_files - successful
        print(f"Total files: {total_files}")
        print(f"Successful: {successful}")
        print(f"Failed: {failed}")
        print(f"Total time: {format_time(elapsed)}")
        
        if failed > 0:
            print("\nFailed files:")
            for file_num, success in results.items():
                if not success:
                    print(f"  - File #{file_num}: {self.files_info[file_num]['filename']}")
        
        return results

def main():
    """Main interactive function"""
    print(" ")
    print("="*66)
    print(f" PeekXtract v{__version__} — Smart Remote ZIP Peeks & Selective Downloads")
    print("      By °⊥⋊ɹ∀° (ArKT) | Telegram: @ArKT_7 | GitHub: ArKT-7")
    print("="*66)
    # print("\nFeatures:")
    # print("   File search/filter with regex support")
    # print("   Bulk download with range selection")
    # print("   CRC32 integrity verification")
    # print("   NEW: Extended compression support:")
    # print(f"    - DEFLATE (compression method 8)")
    # print(f"    - BZIP2 (compression method 12)")
    # print(f"    - LZMA (compression method 14)")
    # print(f"    - STORED (compression method 0)")
    # print("="*59)
    print()

    if len(sys.argv) > 1:
        url = sys.argv[1].strip()
        print(f"Using ZIP file URL from cli input:: {url}")
    else:
        url = input("Enter the ZIP file URL: ").strip()
    if not url:
        print("No URL provided. Exiting.")
        return
    if not (url.startswith("http://") or url.startswith("https://")):
        print("Please provide a valid HTTP/HTTPS URL.")
        return
    
    try:
        zip_reader = EnhancedRemoteZipReader(url)
        print("\nScanning ZIP file contents...")
        zip_reader.list_files()
        
        if not zip_reader.files_info:
            print("No files found to download.")
            return
        
        while True:
            print(f"\n{'='*70}")
            print("Download Options:")
            print(f"- Enter a file number to download")
            print("- Enter range like '1-5' or multiple '1,3,5' or combined '1-5,7,10-12'")
            print("- Enter 'search <pattern>' or 's <pattern>' to filter files (e.g., 'search .img')")
            print("- Enter 'all' to download all files (or 'a')")
            print("- Enter 'list' to show files again (or 'l')")
            print("- Enter 'quit' to exit (or 'q')")

            choice = input("\nYour choice: ").strip()
            
            if choice.lower() in ('quit', 'q'):
                print("\n\nPeekXtract... Goodbye!\n")
                break

            elif choice.lower() in ('list', 'l'):
                zip_reader.list_files()
                continue

            elif choice.lower().startswith('search ') or choice.lower().startswith('s '):
                if choice.lower().startswith('search '):
                    pattern = choice[7:].strip()
                else:
                    pattern = choice[2:].strip()
                if pattern:
                    print(f"\nSearching for files matching: '{pattern}'")
                    matches = zip_reader.search_files(pattern)
                    if matches:
                        print(f"Found {len(matches)} matching files:")
                        zip_reader.list_files(pattern)
                    else:
                        print(f"No files match pattern: '{pattern}'")
                else:
                    print("Please provide a search pattern after 'search ' or 's '")
                continue
            
            elif choice.lower() in ('all', 'a'):
                display_count = len(zip_reader.current_display_mapping) if zip_reader.current_display_mapping else len(zip_reader.files_info)
                confirm = input(f"\nDownload all {display_count} files? (y/n): ").strip().lower()
                if confirm == 'y':
                    verify = input("Verify file integrity with CRC32? (y/n, default=y): ").strip().lower()
                    verify_integrity = verify != 'n'
                    file_nums = list(range(1, display_count + 1))
                    zip_reader.download_bulk(file_nums, verify_integrity=verify_integrity)
                    another = input("\nPerform another operation? (y/n): ").strip().lower()
                    if another != 'y':
                        print("\n\nPeekXtract... Goodbye!\n")
                        break
                continue
            
            if '-' in choice or ',' in choice:
                display_count = len(zip_reader.current_display_mapping) if zip_reader.current_display_mapping else len(zip_reader.files_info)
                file_nums = parse_range(choice, display_count)
                if file_nums:
                    print(f"\nParsed file numbers: {file_nums}")
                    confirm = input(f"Download {len(file_nums)} files? (y/n): ").strip().lower()
                    if confirm == 'y':
                        verify = input("Verify file integrity with CRC32? (y/n, default=y): ").strip().lower()
                        verify_integrity = verify != 'n'
                        zip_reader.download_bulk(file_nums, verify_integrity=verify_integrity)
                        another = input("\nPerform another operation? (y/n): ").strip().lower()
                        if another != 'y':
                            print("\n\nPeekXtract... Goodbye!\n")
                            break
                else:
                    print("Invalid range format or no valid files in range.")
                continue
            
            try:
                file_num = int(choice)
                display_count = len(zip_reader.current_display_mapping) if zip_reader.current_display_mapping else len(zip_reader.files_info)
                if 1 <= file_num <= display_count:
                    verify = input("Verify file integrity with CRC32? (y/n, default=y): ").strip().lower()
                    verify_integrity = verify != 'n'
                    success = zip_reader.download_file(file_num, verify_integrity=verify_integrity)
                    if success:
                        print("\nFile downloaded successfully!")
                        another = input("\nDownload another file? (y/n): ").strip().lower()
                        if another != 'y':
                            print("\n\nPeekXtract... Goodbye!\n")
                            break
                    else:
                        print("Download failed. Please try again.")
                else:
                    print(f"Invalid file number. Please choose between 1 and {display_count}")
            except ValueError:
                print("Invalid input. Please try again.")
    
    except Exception as e:
        print(f"Error: {e}")
        if "501" not in str(e):
            print("\nPlease check your URL and internet connection.\n")

if __name__ == "__main__":
    main()
