"""Compiled selector traversal over a flattened JSON tree.

Python owns the objects and all buffers.  Mojo only walks integer topology,
selector bytecode, and scalar side tables, so the C boundary never owns
memory and never sees Python objects.
"""

from std.sys.info import simd_width_of

comptime IPtr = UnsafePointer[Int64, AnyOrigin[mut=True]]
comptime FPtr = UnsafePointer[Float64, AnyOrigin[mut=True]]

comptime FIELD = 1
comptime INDEX = 2
comptime SLICE = 3
comptime LIST_WILDCARD = 4
comptime FIELD_WILDCARD = 5
comptime FILTER = 6


def compare_number(value: Float64, op: Int, wanted: Float64) -> Bool:
    if op == 1:
        return value == wanted
    if op == 2:
        return value != wanted
    if op == 3:
        return value < wanted
    if op == 4:
        return value <= wanted
    if op == 5:
        return value > wanted
    if op == 6:
        return value >= wanted
    return False


def compare_int(value: Int, op: Int, wanted: Int) -> Bool:
    if op == 1:
        return value == wanted
    if op == 2:
        return value != wanted
    if op == 3:
        return value < wanted
    if op == 4:
        return value <= wanted
    if op == 5:
        return value > wanted
    if op == 6:
        return value >= wanted
    return False


def slice_accept(index: Int, length: Int, start_arg: Int, end_arg: Int,
                 step: Int, missing: Int) -> Bool:
    if step > 0:
        var start = 0 if (missing & 1) else start_arg
        var end = length if (missing & 2) else end_arg
        if start < 0:
            start += length
        if start < 0:
            start = 0
        if start > length:
            start = length
        if end < 0:
            end += length
        if end < 0:
            end = 0
        if end > length:
            end = length
        return index >= start and index < end and (index - start) % step == 0

    var start = length - 1 if (missing & 1) else start_arg
    var end = -1 if (missing & 2) else end_arg
    if not (missing & 1) and start < 0:
        start += length
    if start < 0:
        start = -1
    if start >= length:
        start = length - 1
    if not (missing & 2) and end < 0:
        end += length
    if end < -1:
        end = -1
    if end >= length:
        end = length - 1
    return index <= start and index > end and (start - index) % (-step) == 0


def predicate_matches(
    candidate: Int,
    first: IPtr,
    next_sibling: IPtr,
    key_id: IPtr,
    value_type: IPtr,
    value_string: IPtr,
    value_number: FPtr,
    pool: IPtr,
    path_start: Int,
    path_len: Int,
    op: Int,
    constant_type: Int,
    constant_string: Int,
    constant_number: Float64,
) -> Bool:
    var node = candidate
    for depth in range(path_len):
        var child = Int(first[node])
        var wanted_key = Int(pool[path_start + depth])
        var found = -1
        while child >= 0:
            if Int(key_id[child]) == wanted_key:
                found = child
                break
            child = Int(next_sibling[child])
        if found < 0:
            return False
        node = found

    if op == 0:
        return True
    var actual_type = Int(value_type[node])
    if constant_type == 1:
        if actual_type != 2 and actual_type != 1:
            return False
        return compare_int(Int(value_number[node]), op, Int(constant_number))
    if constant_type == 2:
        if actual_type != 2 and actual_type != 1:
            return False
        return compare_number(value_number[node], op, constant_number)
    if constant_type == 3:
        if actual_type != 3:
            return op == 2
        return compare_int(Int(value_string[node]), op, constant_string)
    if constant_type == 4:
        if actual_type == 1 or actual_type == 2:
            return compare_number(value_number[node], op, constant_number)
        return op == 2
    if constant_type == 5:
        return (actual_type == 0) if op == 1 else (actual_type != 0)
    return False


def find_field_child(
    base: Int,
    wanted: Int,
    child_start: IPtr,
    child_count: IPtr,
    child_nodes: IPtr,
    child_keys: IPtr,
) -> Int:
    comptime W = simd_width_of[DType.float64]()
    var start = Int(child_start[base])
    var count = Int(child_count[base])
    var offset = 0
    while offset + W <= count:
        var keys = child_keys.load[width=W](start + offset)
        var nodes = child_nodes.load[width=W](start + offset)
        var matches = keys.eq(SIMD[DType.int64, W](Int64(wanted)))
        if Int(matches.cast[DType.int64]().reduce_add()) > 0:
            for lane in range(W):
                if Bool(matches[lane]):
                    return Int(nodes[lane])
        offset += W
    while offset < count:
        if Int(child_keys[start + offset]) == wanted:
            return Int(child_nodes[start + offset])
        offset += 1
    return -1


def select_from(
    base: Int,
    opcode: Int,
    arg0: Int,
    arg1: Int,
    arg2: Int,
    arg3: Int,
    arg4: Int,
    number_arg: Float64,
    first: IPtr,
    next_sibling: IPtr,
    kind: IPtr,
    key_id: IPtr,
    item_index: IPtr,
    value_type: IPtr,
    value_string: IPtr,
    value_number: FPtr,
    pool: IPtr,
    child_start: IPtr,
    child_count: IPtr,
    child_nodes: IPtr,
    child_keys: IPtr,
    dst: IPtr,
    written_in: Int,
    capacity: Int,
) -> Int:
    var written = written_in
    if opcode == FIELD:
        for j in range(arg1):
            var wanted = Int(pool[arg0 + j])
            var child = find_field_child(
                base, wanted, child_start, child_count, child_nodes, child_keys
            )
            if child >= 0:
                if written >= capacity:
                    return -1
                dst[written] = Int64(child)
                written += 1
        return written

    if opcode == INDEX:
        var length = Int(child_count[base])
        var start = Int(child_start[base])
        for j in range(arg1):
            var wanted = Int(pool[arg0 + j])
            if wanted < 0:
                wanted += length
            if wanted >= 0 and wanted < length:
                if written >= capacity:
                    return -1
                dst[written] = child_nodes[start + wanted]
                written += 1
        return written

    if opcode == SLICE:
        if Int(kind[base]) != 2:
            if Int(value_type[base]) == 0:
                return written
            return -2
        var length = Int(child_count[base])
        var child_offset = Int(child_start[base])
        var start = arg0
        var end = arg1
        if arg2 > 0:
            if arg3 & 1:
                start = 0
            elif start < 0:
                start += length
            if start < 0:
                start = 0
            if start > length:
                start = length
            if arg3 & 2:
                end = length
            elif end < 0:
                end += length
            if end < 0:
                end = 0
            if end > length:
                end = length
        else:
            if arg3 & 1:
                start = length - 1
            elif start < 0:
                start += length
            if start < 0:
                start = -1
            if start >= length:
                start = length - 1
            if arg3 & 2:
                end = -1
            elif end < 0:
                end += length
            if end < -1:
                end = -1
            if end >= length:
                end = length - 1
        if arg2 > 0:
            var result_count = 0
            if start < end:
                result_count = (end - start + arg2 - 1) // arg2
            if written + result_count > capacity:
                return -1
            if arg2 == 1:
                comptime W = simd_width_of[DType.float64]()
                var offset = 0
                while offset + W <= result_count:
                    var nodes = child_nodes.load[width=W](child_offset + start + offset)
                    dst.store(written + offset, nodes)
                    offset += W
                while offset < result_count:
                    dst[written + offset] = child_nodes[child_offset + start + offset]
                    offset += 1
            else:
                var wanted = start
                var offset = 0
                while wanted < end:
                    dst[written + offset] = child_nodes[child_offset + wanted]
                    wanted += arg2
                    offset += 1
            written += result_count
        else:
            var wanted = start
            while wanted > end:
                if written >= capacity:
                    return -1
                dst[written] = child_nodes[child_offset + wanted]
                written += 1
                wanted += arg2
        return written

    if opcode == LIST_WILDCARD or opcode == FIELD_WILDCARD:
        var expected_kind = 2 if opcode == LIST_WILDCARD else 1
        if Int(kind[base]) != expected_kind:
            if opcode == LIST_WILDCARD and Int(value_type[base]) != 0:
                return -2
            return written
        var count = Int(child_count[base])
        if written + count > capacity:
            return -1
        var start = Int(child_start[base])
        comptime W = simd_width_of[DType.float64]()
        var offset = 0
        while offset + W <= count:
            var nodes = child_nodes.load[width=W](start + offset)
            dst.store(written + offset, nodes)
            offset += W
        while offset < count:
            dst[written + offset] = child_nodes[start + offset]
            offset += 1
        written += count
        return written

    if opcode == FILTER:
        if Int(kind[base]) == 1:
            return -2
        if Int(kind[base]) != 2:
            return written
        var child = Int(first[base])
        while child >= 0:
            if predicate_matches(
                child, first, next_sibling, key_id, value_type, value_string,
                value_number, pool, arg0, arg1, arg2, arg3, arg4, number_arg
            ):
                if written >= capacity:
                    return -1
                dst[written] = Int64(child)
                written += 1
            child = Int(next_sibling[child])
        return written
    return written


@export("mjp_eval")
def mjp_eval(
    first_addr: Int,
    next_addr: Int,
    subtree_end_addr: Int,
    kind_addr: Int,
    key_addr: Int,
    index_addr: Int,
    value_type_addr: Int,
    value_string_addr: Int,
    value_number_addr: Int,
    ops_addr: Int,
    desc_addr: Int,
    arg0_addr: Int,
    arg1_addr: Int,
    arg2_addr: Int,
    arg3_addr: Int,
    arg4_addr: Int,
    number_arg_addr: Int,
    pool_addr: Int,
    child_start_addr: Int,
    child_count_addr: Int,
    child_nodes_addr: Int,
    child_keys_addr: Int,
    step_count: Int,
    node_count: Int,
    work_a_addr: Int,
    work_b_addr: Int,
    capacity: Int,
) abi("C") -> Int:
    if (
        step_count <= 0 or node_count <= 0 or capacity <= 0
        or first_addr == 0 or next_addr == 0 or subtree_end_addr == 0
        or kind_addr == 0 or key_addr == 0 or index_addr == 0
        or value_type_addr == 0 or value_string_addr == 0
        or value_number_addr == 0 or ops_addr == 0 or desc_addr == 0
        or arg0_addr == 0 or arg1_addr == 0 or arg2_addr == 0
        or arg3_addr == 0 or arg4_addr == 0 or number_arg_addr == 0
        or pool_addr == 0 or child_start_addr == 0
        or child_count_addr == 0 or child_nodes_addr == 0
        or child_keys_addr == 0 or work_a_addr == 0 or work_b_addr == 0
    ):
        return -3
    var first = IPtr(unsafe_from_address=first_addr)
    var next_sibling = IPtr(unsafe_from_address=next_addr)
    var subtree_end = IPtr(unsafe_from_address=subtree_end_addr)
    var kind = IPtr(unsafe_from_address=kind_addr)
    var key_id = IPtr(unsafe_from_address=key_addr)
    var item_index = IPtr(unsafe_from_address=index_addr)
    var value_type = IPtr(unsafe_from_address=value_type_addr)
    var value_string = IPtr(unsafe_from_address=value_string_addr)
    var value_number = FPtr(unsafe_from_address=value_number_addr)
    var ops = IPtr(unsafe_from_address=ops_addr)
    var desc = IPtr(unsafe_from_address=desc_addr)
    var arg0 = IPtr(unsafe_from_address=arg0_addr)
    var arg1 = IPtr(unsafe_from_address=arg1_addr)
    var arg2 = IPtr(unsafe_from_address=arg2_addr)
    var arg3 = IPtr(unsafe_from_address=arg3_addr)
    var arg4 = IPtr(unsafe_from_address=arg4_addr)
    var number_arg = FPtr(unsafe_from_address=number_arg_addr)
    var pool = IPtr(unsafe_from_address=pool_addr)
    var child_start = IPtr(unsafe_from_address=child_start_addr)
    var child_count = IPtr(unsafe_from_address=child_count_addr)
    var child_nodes = IPtr(unsafe_from_address=child_nodes_addr)
    var child_keys = IPtr(unsafe_from_address=child_keys_addr)
    var work_a = IPtr(unsafe_from_address=work_a_addr)
    var work_b = IPtr(unsafe_from_address=work_b_addr)
    work_a[0] = Int64(0)
    var current_count = 1

    for step in range(step_count):
        var src = work_a if step % 2 == 0 else work_b
        var dst = work_b if step % 2 == 0 else work_a
        var written = 0
        for i in range(current_count):
            var base = Int(src[i])
            if Int(desc[step]) != 0:
                for candidate_base in range(base, Int(subtree_end[base])):
                    written = select_from(
                        candidate_base, Int(ops[step]), Int(arg0[step]),
                        Int(arg1[step]), Int(arg2[step]), Int(arg3[step]),
                        Int(arg4[step]), number_arg[step], first, next_sibling,
                        kind, key_id, item_index, value_type, value_string,
                        value_number, pool, child_start, child_count,
                        child_nodes, child_keys, dst, written, capacity
                    )
                    if written < 0:
                        return written
            else:
                written = select_from(
                    base, Int(ops[step]), Int(arg0[step]), Int(arg1[step]),
                    Int(arg2[step]), Int(arg3[step]), Int(arg4[step]),
                    number_arg[step], first, next_sibling, kind, key_id,
                    item_index, value_type, value_string, value_number, pool,
                    child_start, child_count, child_nodes, child_keys, dst,
                    written, capacity
                )
                if written < 0:
                    return written
        current_count = written
        if current_count == 0:
            break
    return current_count
