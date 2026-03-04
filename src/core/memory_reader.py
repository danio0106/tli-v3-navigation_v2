import struct
import ctypes
import ctypes.wintypes
import time
from typing import Optional, List, Tuple, Any

from src.utils.logger import log

try:
    import pymem
    import pymem.process
    PYMEM_AVAILABLE = True
except ImportError:
    PYMEM_AVAILABLE = False
    log.warning("pymem not available - memory reading disabled")

MEM_COMMIT = 0x1000
PAGE_READABLE = {0x02, 0x04, 0x06, 0x20, 0x40, 0x80}

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.wintypes.DWORD),
        ("Protect", ctypes.wintypes.DWORD),
        ("Type", ctypes.wintypes.DWORD),
    ]


class PointerChain:
    def __init__(self, base_module: str, base_offset: int, offsets: List[int], value_type: str = "int"):
        self.base_module = base_module
        self.base_offset = base_offset
        self.offsets = offsets
        self.value_type = value_type

    def to_dict(self) -> dict:
        return {
            "base_module": self.base_module,
            "base_offset": self.base_offset,
            "offsets": self.offsets,
            "value_type": self.value_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PointerChain":
        return cls(
            base_module=data["base_module"],
            base_offset=data["base_offset"],
            offsets=data["offsets"],
            value_type=data.get("value_type", "int"),
        )


class MemoryReader:
    TYPE_FORMATS = {
        "byte": ("B", 1),
        "short": ("h", 2),
        "ushort": ("H", 2),
        "int": ("i", 4),
        "uint": ("I", 4),
        "long": ("q", 8),
        "ulong": ("Q", 8),
        "float": ("f", 4),
        "double": ("d", 8),
    }

    def __init__(self):
        self._pm: Optional[Any] = None
        self._process_name: str = ""
        self._attached = False
        self._module_bases: dict = {}
        self._read_fail_count: int = 0
        self._read_fail_last_log_t: float = 0.0

    def _log_read_fail(self, kind: str, address: int, detail: str):
        """Throttle repetitive read-failure spam to protect runtime IO/CPU."""
        self._read_fail_count += 1
        now = time.time()
        if now - self._read_fail_last_log_t >= 2.0:
            suppressed = max(0, self._read_fail_count - 1)
            suffix = f" | suppressed={suppressed}" if suppressed > 0 else ""
            log.debug(f"{kind} failed at 0x{address:X}: {detail}{suffix}")
            self._read_fail_last_log_t = now
            self._read_fail_count = 0

    @property
    def is_attached(self) -> bool:
        return self._attached

    @property
    def process_name(self) -> str:
        return self._process_name

    @property
    def process_id(self) -> Optional[int]:
        if self._pm:
            return self._pm.process_id
        return None

    def attach(self, process_name: str) -> bool:
        success, _ = self.attach_with_reason(process_name)
        return success

    def attach_with_reason(self, process_name: str) -> tuple:
        if not PYMEM_AVAILABLE:
            return (False, "pymem is not installed - required for memory reading")

        try:
            self._pm = pymem.Pymem(process_name)
            self._process_name = process_name
            self._attached = True
            self._module_bases.clear()
            log.info(f"Attached to process: {process_name} (PID: {self._pm.process_id})")
            return (True, "")
        except pymem.exception.ProcessNotFound:
            self._attached = False
            return (False, f"Process '{process_name}' not found. Make sure the game is running.")
        except pymem.exception.CouldNotOpenProcess:
            self._attached = False
            return (False, f"Access denied for '{process_name}'. Run the bot as Administrator.")
        except Exception as e:
            self._attached = False
            return (False, f"Unexpected error: {e}")

    def detach(self):
        if not self._attached and self._pm is None:
            return
        if self._pm:
            try:
                if self._pm.process_handle:
                    self._pm.close_process()
            except Exception as e:
                log.debug(f"Error closing process handle: {e}")
        self._pm = None
        self._attached = False
        self._module_bases.clear()
        log.info("Detached from process")

    def __del__(self):
        try:
            self.detach()
        except Exception as e:
            log.debug(f"Error during MemoryReader cleanup: {e}")

    def get_module_base(self, module_name: str) -> Optional[int]:
        if not self._attached or not self._pm:
            return None

        if module_name in self._module_bases:
            return self._module_bases[module_name]

        try:
            module = pymem.process.module_from_name(
                self._pm.process_handle, module_name
            )
            if module:
                self._module_bases[module_name] = module.lpBaseOfDll
                log.debug(f"Module {module_name} base: 0x{module.lpBaseOfDll:X}")
                return module.lpBaseOfDll
        except Exception as e:
            log.error(f"Failed to get module base for {module_name}: {e}")
        return None

    def list_modules(self) -> List[Tuple[str, int, int]]:
        if not self._attached or not self._pm:
            return []
        try:
            modules = []
            for module in pymem.process.enum_process_module(self._pm.process_handle):
                modules.append((
                    module.name,
                    module.lpBaseOfDll,
                    module.SizeOfImage,
                ))
            return modules
        except Exception as e:
            log.error(f"Failed to list modules: {e}")
            return []

    def enumerate_memory_regions(self, min_addr: int = 0x10000, max_addr: int = 0x7FFFFFFFFFFF) -> List[Tuple[int, int]]:
        if not self._attached or not self._pm:
            return []
        try:
            kernel32 = ctypes.windll.kernel32
            handle = self._pm.process_handle
            regions = []
            addr = min_addr
            mbi = MEMORY_BASIC_INFORMATION()
            mbi_size = ctypes.sizeof(mbi)

            while addr < max_addr:
                result = kernel32.VirtualQueryEx(
                    handle, ctypes.c_void_p(addr), ctypes.byref(mbi), mbi_size
                )
                if result == 0:
                    break

                if (mbi.State == MEM_COMMIT and
                    mbi.Protect in PAGE_READABLE and
                    mbi.RegionSize > 0 and
                    mbi.RegionSize < 0x10000000):
                    regions.append((mbi.BaseAddress, mbi.RegionSize))

                addr = mbi.BaseAddress + mbi.RegionSize
                if addr <= mbi.BaseAddress:
                    break

            return regions
        except Exception as e:
            log.error(f"Failed to enumerate memory regions: {e}")
            return []

    @property
    def process_handle(self):
        if self._pm:
            return self._pm.process_handle
        return None

    def read_bytes(self, address: int, size: int) -> Optional[bytes]:
        if not self._attached or not self._pm:
            return None
        try:
            return self._pm.read_bytes(address, size)
        except Exception as e:
            self._log_read_fail("read_bytes", address, f"size={size} err={e}")
            return None

    def read_value(self, address: int, value_type: str = "int") -> Optional[Any]:
        if not self._attached or not self._pm:
            return None

        fmt_info = self.TYPE_FORMATS.get(value_type)
        if not fmt_info:
            log.error(f"Unknown value type: {value_type}")
            return None

        fmt, size = fmt_info
        try:
            data = self._pm.read_bytes(address, size)
            if data:
                return struct.unpack(f"<{fmt}", data)[0]
        except Exception as e:
            self._log_read_fail("read_value", address, f"type={value_type} err={e}")
            return None
        return None

    def read_string(self, address: int, max_length: int = 256, encoding: str = "utf-8") -> Optional[str]:
        if not self._attached or not self._pm:
            return None
        try:
            data = self._pm.read_bytes(address, max_length)
            if data:
                null_idx = data.find(b"\x00")
                if null_idx >= 0:
                    data = data[:null_idx]
                return data.decode(encoding, errors="replace")
        except Exception as e:
            log.debug(f"read_string failed at 0x{address:X}: {e}")
            return None
        return None

    def resolve_pointer_chain(self, chain: PointerChain) -> Optional[int]:
        if chain.base_module == "__absolute__":
            address = chain.base_offset
        else:
            base = self.get_module_base(chain.base_module)
            if base is None:
                return None
            address = base + chain.base_offset

        for offset in chain.offsets:
            ptr = self.read_value(address, "ulong")
            if ptr is None or ptr == 0:
                return None
            address = ptr + offset

        return address

    def read_pointer_chain(self, chain: PointerChain) -> Optional[Any]:
        address = self.resolve_pointer_chain(chain)
        if address is None:
            return None
        return self.read_value(address, chain.value_type)

    def scan_value(self, value: Any, value_type: str, module_name: Optional[str] = None,
                   start: int = 0, size: int = 0) -> List[int]:
        if not self._attached or not self._pm:
            return []

        fmt_info = self.TYPE_FORMATS.get(value_type)
        if not fmt_info:
            return []

        fmt, val_size = fmt_info
        search_bytes = struct.pack(f"<{fmt}", value)

        if module_name:
            base = self.get_module_base(module_name)
            if base is None:
                return []
            try:
                module = pymem.process.module_from_name(
                    self._pm.process_handle, module_name
                )
                start = base
                size = module.SizeOfImage
            except Exception as e:
                log.debug(f"scan_value module lookup failed for {module_name}: {e}")
                return []
        elif start == 0:
            return []

        results = []
        chunk_size = 65536
        offset = 0

        while offset < size:
            read_size = min(chunk_size, size - offset)
            try:
                data = self._pm.read_bytes(start + offset, read_size)
                if data:
                    idx = 0
                    while True:
                        idx = data.find(search_bytes, idx)
                        if idx == -1:
                            break
                        results.append(start + offset + idx)
                        idx += 1
            except Exception as e:
                log.debug(f"scan_value chunk read failed at 0x{start + offset:X}: {e}")
            offset += chunk_size

        return results

    def scan_changed(self, addresses: List[int], old_value: Any, new_value: Any,
                     value_type: str) -> List[int]:
        if not self._attached or not self._pm:
            return []

        results = []
        for addr in addresses:
            current = self.read_value(addr, value_type)
            if current == new_value:
                results.append(addr)
        return results

    def read_fname(self, fnamepool_base: int, fname_index: int) -> str:
        """Read a name from FNamePool (UE4 4.23+).
        
        FNamePool layout:
        - 0x00: Lock (8 bytes)
        - 0x08: CurrentBlock (uint32)
        - 0x0C: CurrentByteCursor (uint32)
        - 0x10: Blocks[8192] (array of pointers)
        
        FName index decoding:
        - block_idx = index >> 16
        - block_offset = index & 0xFFFF
        - entry_addr = Blocks[block_idx] + block_offset * 2
        
        FNameEntry header (uint16):
        - bit 0: bIsWide
        - bits 1-5: LowercaseProbeHash (5 bits)
        - bits 6-15: Len (10 bits)
        """
        if not self._attached or not self._pm:
            return ""

        if fname_index <= 0:
            return ""

        try:
            block_idx = fname_index >> 16
            block_offset = fname_index & 0xFFFF

            if block_idx >= 8192:
                return ""

            block_ptr_addr = fnamepool_base + 0x10 + block_idx * 8
            block_ptr = self.read_value(block_ptr_addr, "ulong")
            if not block_ptr or block_ptr < 0x10000:
                return ""

            entry_addr = block_ptr + block_offset * 2

            header = self.read_value(entry_addr, "ushort")
            if header is None:
                return ""

            is_wide = header & 1
            name_len = (header >> 6) & 0x3FF

            if name_len <= 0 or name_len > 1024:
                return ""

            name_addr = entry_addr + 2

            if is_wide:
                raw = self.read_bytes(name_addr, name_len * 2)
                if not raw:
                    return ""
                try:
                    return raw.decode("utf-16-le").rstrip("\x00")
                except Exception as e:
                    log.debug(f"read_fname wide decode failed for index {fname_index}: {e}")
                    return ""
            else:
                raw = self.read_bytes(name_addr, name_len)
                if not raw:
                    return ""
                try:
                    return raw.decode("utf-8", errors="replace").rstrip("\x00")
                except Exception as e:
                    log.debug(f"read_fname utf-8 decode failed for index {fname_index}: {e}")
                    return ""
        except Exception as e:
            log.debug(f"read_fname failed for index {fname_index}: {e}")
            return ""

    def find_gobject_by_name(self, gobjects_base: int, fnamepool_base: int,
                             target_name: str, max_objects: int = 500000) -> list:
        """Find UObject instances by name using GObjects array and FNamePool.
        
        GObjects (FUObjectArray) layout:
        - 0x00: FUObjectItem** Objects (pointer to chunk pointer array)
        - 0x08: FUObjectItem* PreAllocatedObjects
        - 0x10: int32 MaxElements
        - 0x14: int32 NumElements
        - 0x18: int32 MaxChunks
        - 0x1C: int32 NumChunks
        
        FUObjectItem layout (24 bytes):
        - 0x00: UObject* Object (8 bytes)
        - 0x08: int32 Flags (4 bytes)
        - 0x0C: int32 ClusterRootIndex (4 bytes)
        - 0x10: int32 SerialNumber (4 bytes)
        - 0x14: padding (4 bytes)
        
        UObject layout:
        - 0x00: VfTable (8 bytes)
        - 0x08: int32 ObjectFlags (4 bytes)
        - 0x0C: int32 InternalIndex (4 bytes)
        - 0x10: UClass* ClassPrivate (8 bytes)
        - 0x18: FName NamePrivate (ComparisonIndex: int32, Number: int32)
        - 0x20: UObject* OuterPrivate (8 bytes)
        
        Returns list of (object_address, full_name) tuples.
        """
        if not self._attached or not self._pm:
            return []

        results = []

        try:
            objects_chunks_ptr = self.read_value(gobjects_base, "ulong")
            num_elements = self.read_value(gobjects_base + 0x14, "int")
            num_chunks = self.read_value(gobjects_base + 0x1C, "int")

            if not objects_chunks_ptr or not num_elements or not num_chunks:
                log.debug(f"[GObjects] Failed to read header at 0x{gobjects_base:X}")
                return []

            if num_elements <= 0 or num_elements > max_objects:
                log.debug(f"[GObjects] Unexpected NumElements: {num_elements}")
                return []

            log.debug(f"[GObjects] NumElements={num_elements}, NumChunks={num_chunks}")

            elements_per_chunk = 65536
            item_size = 24
            target_name_lower = target_name.lower()

            fname_cache = {}

            for chunk_idx in range(num_chunks):
                chunk_ptr = self.read_value(objects_chunks_ptr + chunk_idx * 8, "ulong")
                if not chunk_ptr or chunk_ptr < 0x10000:
                    continue

                start_idx = chunk_idx * elements_per_chunk
                end_idx = min(start_idx + elements_per_chunk, num_elements)
                count_in_chunk = end_idx - start_idx

                chunk_data = self.read_bytes(chunk_ptr, count_in_chunk * item_size)
                if not chunk_data:
                    continue

                for i in range(count_in_chunk):
                    item_offset = i * item_size
                    obj_ptr_bytes = chunk_data[item_offset:item_offset + 8]
                    if len(obj_ptr_bytes) < 8:
                        continue

                    obj_ptr = struct.unpack("<Q", obj_ptr_bytes)[0]
                    if obj_ptr < 0x10000 or obj_ptr > 0x7FFFFFFFFFFF:
                        continue

                    fname_data = self.read_bytes(obj_ptr + 0x18, 4)
                    if not fname_data:
                        continue

                    comparison_index = struct.unpack("<i", fname_data)[0]

                    if comparison_index in fname_cache:
                        name = fname_cache[comparison_index]
                    else:
                        name = self.read_fname(fnamepool_base, comparison_index)
                        fname_cache[comparison_index] = name

                    if name.lower() == target_name_lower:
                        results.append((obj_ptr, name))

            log.debug(f"[GObjects] Found {len(results)} objects named '{target_name}'")
            return results

        except Exception as e:
            log.error(f"[GObjects] Error iterating: {e}")
            return []

    def find_gobjects_by_class_name(self, gobjects_base: int, fnamepool_base: int,
                                     target_class_name: str, max_objects: int = 500000) -> list:
        """Find UObject instances whose CLASS has a given FName.

        Unlike find_gobject_by_name which matches the instance's own FName,
        this matches by the class pointer's FName. This finds actual instances
        of a class, not the class definition UObject itself.

        UObject layout:
        - 0x10: UClass* ClassPrivate (8 bytes)
        - ClassPrivate+0x18: FName of the class

        Returns list of (object_address, instance_name) tuples.
        """
        if not self._attached or not self._pm:
            return []

        results = []

        try:
            objects_chunks_ptr = self.read_value(gobjects_base, "ulong")
            num_elements = self.read_value(gobjects_base + 0x14, "int")
            num_chunks = self.read_value(gobjects_base + 0x1C, "int")

            if not objects_chunks_ptr or not num_elements or not num_chunks:
                return []

            if num_elements <= 0 or num_elements > max_objects:
                return []

            elements_per_chunk = 65536
            item_size = 24
            target_lower = target_class_name.lower()

            fname_cache = {}
            class_name_cache = {}

            for chunk_idx in range(num_chunks):
                chunk_ptr = self.read_value(objects_chunks_ptr + chunk_idx * 8, "ulong")
                if not chunk_ptr or chunk_ptr < 0x10000:
                    continue

                start_idx = chunk_idx * elements_per_chunk
                end_idx = min(start_idx + elements_per_chunk, num_elements)
                count_in_chunk = end_idx - start_idx

                chunk_data = self.read_bytes(chunk_ptr, count_in_chunk * item_size)
                if not chunk_data:
                    continue

                for i in range(count_in_chunk):
                    item_offset = i * item_size
                    obj_ptr_bytes = chunk_data[item_offset:item_offset + 8]
                    if len(obj_ptr_bytes) < 8:
                        continue

                    obj_ptr = struct.unpack("<Q", obj_ptr_bytes)[0]
                    if obj_ptr < 0x10000 or obj_ptr > 0x7FFFFFFFFFFF:
                        continue

                    class_ptr_data = self.read_bytes(obj_ptr + 0x10, 8)
                    if not class_ptr_data:
                        continue
                    class_ptr = struct.unpack("<Q", class_ptr_data)[0]
                    if class_ptr < 0x10000 or class_ptr > 0x7FFFFFFFFFFF:
                        continue

                    if class_ptr in class_name_cache:
                        class_name = class_name_cache[class_ptr]
                    else:
                        cls_fname_data = self.read_bytes(class_ptr + 0x18, 4)
                        if not cls_fname_data:
                            class_name_cache[class_ptr] = ""
                            continue
                        cls_comparison_index = struct.unpack("<i", cls_fname_data)[0]
                        class_name = self.read_fname(fnamepool_base, cls_comparison_index)
                        class_name_cache[class_ptr] = class_name

                    if class_name.lower() == target_lower:
                        fname_data = self.read_bytes(obj_ptr + 0x18, 4)
                        inst_name = ""
                        if fname_data:
                            comp_idx = struct.unpack("<i", fname_data)[0]
                            if comp_idx in fname_cache:
                                inst_name = fname_cache[comp_idx]
                            else:
                                inst_name = self.read_fname(fnamepool_base, comp_idx)
                                fname_cache[comp_idx] = inst_name
                        results.append((obj_ptr, inst_name))

            log.debug(f"[GObjects] Found {len(results)} instances of class '{target_class_name}'")
            return results

        except Exception as e:
            log.error(f"[GObjects] Error in class search: {e}")
            return []

    def read_uobject_name(self, fnamepool_base: int, obj_ptr: int) -> str:
        """Read the FName of a UObject and resolve it via FNamePool."""
        if not obj_ptr or obj_ptr < 0x10000:
            return ""
        fname_data = self.read_bytes(obj_ptr + 0x18, 4)
        if not fname_data:
            return ""
        comparison_index = struct.unpack("<i", fname_data)[0]
        return self.read_fname(fnamepool_base, comparison_index)

    def search_gobjects_by_keywords(self, gobjects_base: int, fnamepool_base: int,
                                      keywords: list, max_objects: int = 500000,
                                      progress_callback=None) -> list:
        """Search all GObjects for keyword matches in object name, class name, and outer name.
        
        For each UObject, checks three names against keyword list:
        - Object's own FName (+0x18)
        - Class FName (+0x10 -> class ptr -> +0x18)
        - Outer FName (+0x20 -> outer ptr -> +0x18)
        
        Returns list of dicts: {address, obj_name, class_name, outer_name, matched_keyword}
        """
        if not self._attached or not self._pm:
            return []

        results = []
        keywords_lower = [k.lower() for k in keywords]

        try:
            objects_chunks_ptr = self.read_value(gobjects_base, "ulong")
            num_elements = self.read_value(gobjects_base + 0x14, "int")
            num_chunks = self.read_value(gobjects_base + 0x1C, "int")

            if not objects_chunks_ptr or not num_elements or not num_chunks:
                log.error(f"[KeywordScan] Failed to read GObjects header at 0x{gobjects_base:X}")
                return []

            if num_elements <= 0 or num_elements > max_objects:
                log.error(f"[KeywordScan] Unexpected NumElements: {num_elements}")
                return []

            if progress_callback:
                progress_callback(f"Scanning {num_elements} objects across {num_chunks} chunks...")

            elements_per_chunk = 65536
            item_size = 24
            fname_cache = {}
            scanned = 0

            def resolve_fname(index):
                if index in fname_cache:
                    return fname_cache[index]
                name = self.read_fname(fnamepool_base, index)
                fname_cache[index] = name
                return name

            for chunk_idx in range(num_chunks):
                chunk_ptr = self.read_value(objects_chunks_ptr + chunk_idx * 8, "ulong")
                if not chunk_ptr or chunk_ptr < 0x10000:
                    continue

                start_idx = chunk_idx * elements_per_chunk
                end_idx = min(start_idx + elements_per_chunk, num_elements)
                count_in_chunk = end_idx - start_idx

                chunk_data = self.read_bytes(chunk_ptr, count_in_chunk * item_size)
                if not chunk_data:
                    continue

                for i in range(count_in_chunk):
                    item_offset = i * item_size
                    obj_ptr_bytes = chunk_data[item_offset:item_offset + 8]
                    if len(obj_ptr_bytes) < 8:
                        continue

                    obj_ptr = struct.unpack("<Q", obj_ptr_bytes)[0]
                    if obj_ptr < 0x10000 or obj_ptr > 0x7FFFFFFFFFFF:
                        continue

                    obj_header = self.read_bytes(obj_ptr + 0x10, 0x18)
                    if not obj_header or len(obj_header) < 0x18:
                        continue

                    class_ptr = struct.unpack("<Q", obj_header[0:8])[0]
                    obj_fname_idx = struct.unpack("<i", obj_header[8:12])[0]
                    outer_ptr = struct.unpack("<Q", obj_header[0x10:0x18])[0]

                    obj_name = resolve_fname(obj_fname_idx)

                    class_name = ""
                    if class_ptr and 0x10000 < class_ptr < 0x7FFFFFFFFFFF:
                        cls_fname_data = self.read_bytes(class_ptr + 0x18, 4)
                        if cls_fname_data:
                            cls_fname_idx = struct.unpack("<i", cls_fname_data)[0]
                            class_name = resolve_fname(cls_fname_idx)

                    outer_name = ""
                    if outer_ptr and 0x10000 < outer_ptr < 0x7FFFFFFFFFFF:
                        outer_fname_data = self.read_bytes(outer_ptr + 0x18, 4)
                        if outer_fname_data:
                            outer_fname_idx = struct.unpack("<i", outer_fname_data)[0]
                            outer_name = resolve_fname(outer_fname_idx)

                    combined = f"{obj_name}|{class_name}|{outer_name}".lower()
                    for kw in keywords_lower:
                        if kw in combined:
                            results.append({
                                "address": obj_ptr,
                                "obj_name": obj_name,
                                "class_name": class_name,
                                "outer_name": outer_name,
                                "matched_keyword": kw,
                                "index": start_idx + i,
                            })
                            break

                scanned += count_in_chunk
                if progress_callback and chunk_idx % 2 == 0:
                    progress_callback(f"  Scanned {scanned}/{num_elements} objects, {len(results)} hits...")

            if progress_callback:
                progress_callback(f"Scan complete: {len(results)} matches from {scanned} objects")

            return results

        except Exception as e:
            log.error(f"[KeywordScan] Error: {e}")
            return []

    def dump_class_properties(self, fnamepool_base: int, obj_ptr: int,
                              progress_callback=None) -> list:
        """Walk UClass property chain to enumerate all named fields.
        
        Given a UObject address:
        1. Read its UClass pointer (+0x10)
        2. Try UProperty chain via UStruct::Children (+0x38, UField::Next +0x28)
        3. Try FProperty chain via UStruct::ChildProperties (+0x50, FField::Next +0x20)
        4. For each property: read name (FName) and offset within instance
        
        Returns list of dicts: {name, offset, size, prop_address, chain_type}
        """
        if not self._attached or not self._pm:
            return []

        results = []

        try:
            class_ptr = self.read_value(obj_ptr + 0x10, "ulong")
            if not class_ptr or class_ptr < 0x10000:
                if progress_callback:
                    progress_callback(f"Failed to read UClass at obj+0x10")
                return []

            class_name = self.read_uobject_name(fnamepool_base, class_ptr)
            if progress_callback:
                progress_callback(f"Class: {class_name} at 0x{class_ptr:X}")

            children_ptr = self.read_value(class_ptr + 0x38, "ulong")
            if children_ptr and 0x10000 < children_ptr < 0x7FFFFFFFFFFF:
                if progress_callback:
                    progress_callback(f"Walking UProperty chain (Children at +0x38)...")
                prop_ptr = children_ptr
                visited = set()
                while prop_ptr and 0x10000 < prop_ptr < 0x7FFFFFFFFFFF and prop_ptr not in visited:
                    visited.add(prop_ptr)

                    prop_fname_data = self.read_bytes(prop_ptr + 0x18, 4)
                    if not prop_fname_data:
                        break
                    prop_fname_idx = struct.unpack("<i", prop_fname_data)[0]
                    prop_name = self.read_fname(fnamepool_base, prop_fname_idx)

                    prop_offset = self.read_value(prop_ptr + 0x44, "int")
                    prop_size = self.read_value(prop_ptr + 0x34, "int")

                    if prop_name and prop_offset is not None:
                        results.append({
                            "name": prop_name,
                            "offset": prop_offset,
                            "size": prop_size if prop_size else 0,
                            "prop_address": prop_ptr,
                            "chain_type": "UProperty",
                        })

                    next_ptr = self.read_value(prop_ptr + 0x28, "ulong")
                    prop_ptr = next_ptr

            for child_offset in [0x50, 0x48, 0x40]:
                fprop_ptr = self.read_value(class_ptr + child_offset, "ulong")
                if not fprop_ptr or fprop_ptr < 0x10000 or fprop_ptr > 0x7FFFFFFFFFFF:
                    continue

                test_name_data = self.read_bytes(fprop_ptr + 0x28, 4)
                if not test_name_data:
                    continue
                test_idx = struct.unpack("<i", test_name_data)[0]
                test_name = self.read_fname(fnamepool_base, test_idx)
                if not test_name or not test_name.replace("_", "").replace("-", "").isalnum():
                    continue

                if progress_callback:
                    progress_callback(f"Walking FProperty chain (ChildProperties at +0x{child_offset:X})...")
                visited_f = set()
                fp = fprop_ptr
                while fp and 0x10000 < fp < 0x7FFFFFFFFFFF and fp not in visited_f:
                    visited_f.add(fp)

                    fp_fname_data = self.read_bytes(fp + 0x28, 4)
                    if not fp_fname_data:
                        break
                    fp_fname_idx = struct.unpack("<i", fp_fname_data)[0]
                    fp_name = self.read_fname(fnamepool_base, fp_fname_idx)

                    fp_offset = self.read_value(fp + 0x4C, "int")
                    fp_size = self.read_value(fp + 0x38, "int")

                    if fp_name and fp_offset is not None:
                        already = any(r["name"] == fp_name and r["offset"] == fp_offset for r in results)
                        if not already:
                            results.append({
                                "name": fp_name,
                                "offset": fp_offset,
                                "size": fp_size if fp_size else 0,
                                "prop_address": fp,
                                "chain_type": "FProperty",
                            })

                    next_fp = self.read_value(fp + 0x20, "ulong")
                    fp = next_fp

                if len(visited_f) > 1:
                    break

            super_ptr = self.read_value(class_ptr + 0x30, "ulong")
            if super_ptr and 0x10000 < super_ptr < 0x7FFFFFFFFFFF and super_ptr != class_ptr:
                super_name = self.read_uobject_name(fnamepool_base, super_ptr)
                if progress_callback and super_name:
                    progress_callback(f"  Super class: {super_name}")

            results.sort(key=lambda r: r["offset"])

            if progress_callback:
                progress_callback(f"Found {len(results)} properties")

            return results

        except Exception as e:
            log.error(f"[DumpProperties] Error: {e}")
            return []

    def write_value(self, address: int, value: Any, value_type: str = "int") -> bool:
        if not self._attached or not self._pm:
            return False

        fmt_info = self.TYPE_FORMATS.get(value_type)
        if not fmt_info:
            return False

        fmt, size = fmt_info
        try:
            data = struct.pack(f"<{fmt}", value)
            self._pm.write_bytes(address, data, size)
            return True
        except Exception as e:
            log.error(f"Failed to write at 0x{address:X}: {e}")
            return False
