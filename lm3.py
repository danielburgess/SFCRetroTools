from snes import SFCAddress, SFCAddressType
from script import Table

"""
Little Master 3
TODO: Find more pointer locations.
      Extract old script for comparison.
      Work on script. 
      Work on font.
      VWF?
"""


def extract_script_bins(file_name='base.sfc', folder_prefix='en', table_filename='jap.tbl'):
    folder_name = f'{folder_prefix}_ptr_data'

    # main script data
    extract_pointer_data(file_name, 0x1B0000, 0x400, 'script', folder_name, table=table_filename)

    # Scenario descriptions
    extract_pointer_data(file_name, 0x111EE3, 0x13C, 'scenario-desc', folder_name, table=table_filename)

    # 0x1456B - Unit Attribute Value Pointer (0x3 length) - 2 byte height, weight, 1 byte age
    # 0x1457F - Unit Weapon Name Pointer - Preceeding byte is entry length
    # 0x14588 - Unit Armor Name Pointer - Preceeding byte is entry length

    # unit and terrain descriptions
    extract_pointer_data(file_name, 0x30000, 0x500, 'unit-terrain-desc', folder_name, table=table_filename)

    # data for unit attacks that are re-used (they seem to be exact) between the 5 unit type tables
    atk_data = extract_pointer_data(file_name, 0x1B0800, 0x6A, 'unit-attacks', folder_name,
                                    output=False, table=table_filename)
    atk_data = extract_pointer_data(file_name, 0x1B0A00, 0x6A, 'unit-attacks', folder_name, atk_data,
                                    output=False, table=table_filename)
    atk_data = extract_pointer_data(file_name, 0x1B0C00, 0x6A, 'unit-attacks', folder_name, atk_data,
                                    output=False, table=table_filename)
    atk_data = extract_pointer_data(file_name, 0x1B0E00, 0x6A, 'unit-attacks', folder_name, atk_data,
                                    output=False, table=table_filename)
    extract_pointer_data(file_name, 0x1B1000, 0x6A, 'unit-attacks', folder_name, atk_data, table=table_filename)


def extract_pointer_data(input_filename: str, ptr_tbl_pos: int, tbl_len: int, table_name: str, out_folder='out',
                         ptr_data: dict = None, output=True, table: str = None):
    data_file = open(input_filename, "rb")
    bin_data = list(data_file.read())
    return pointer_extract(table_name, out_folder, bin_data, ptr_tbl_pos, tbl_len,
                           ptr_data=ptr_data, output=output, table=table)


def pointer_extract(table_name: str, out_folder: str, bin_data: list, ptr_tbl_loc: int, ptr_tbl_len: int = None,
                    ptr_bytes: int = 2, ptr_bank: int = None, ptr_data: dict = None, output=True, table: str = None,
                    addr_type: int = 4):
    """
    Extract data from the pointer table
    :param table_name:
    :param out_folder:
    :param bin_data:
    :param ptr_tbl_loc:
    :param ptr_tbl_len:
    :param ptr_bytes:
    :param ptr_bank:
    :param ptr_data:
    :param output: if we are dumping
    :param table: character table class or None if only binary data
    :param addr_type: location value to show for the text output
    0=Table PC Address with index
    1=Table Index Only
    2=Pointer Address
    3=Block Address
    4=Combines 0 with 3
    :return:
    """

    import os
    if not ptr_tbl_len:
        ptr_tbl_len = 0x1000

    ptr_table_addr = SFCAddress(ptr_tbl_loc)
    if not ptr_bank:
        ptr_bank = ptr_table_addr.get_bank_byte(SFCAddressType.LOROM1)
    if not ptr_data:
        ptr_data = {}
    tbl = Table(table) if table else None

    try:
        if not os.path.isdir(out_folder):
            os.mkdir(out_folder)
            print(f'Info: Created output folder. "{out_folder}"')
    except OSError as error:
        print(f'Warning: Cannot create output folder. "{out_folder}"')

    table_folder = f'{out_folder}/{table_name}'
    try:
        if not os.path.isdir(table_folder):
            os.mkdir(table_folder)
            print(f'Info: Created output folder. "{table_folder}"')
    except OSError as error:
        print(f'Warning: Cannot create output folder. "{table_folder}"')

    pointer_list = ptr_data['ptr_list'] if ptr_data else []
    bin_list = ptr_data['bin_list'] if ptr_data else []

    ptr_index = 0
    for i in range(ptr_tbl_loc, ptr_tbl_loc + ptr_tbl_len, ptr_bytes):
        # get the bytes for the pointer
        ptr_end = i + ptr_bytes
        this_ptr_data = bin_data[i: ptr_end]

        # convert the pointer to an SFCAddress object
        ptr = SFCAddress([this_ptr_data[0], this_ptr_data[1], ptr_bank], SFCAddressType.LOROM1)

        # convert the address objects for the current and next pointer to data start/stop positions
        data_start = ptr.get_address(SFCAddressType.PC)
        data_end = data_start

        found = False
        while not found:
            try:
                end_inc = tbl.check_for_lone_byte(bin_data, data_end, 0x0)
                if end_inc == -1:
                    found = True
                elif end_inc > 0:
                    data_end += end_inc - 1
            except IndexError:
                data_end -= 2
                found = True
            data_end += 1

        data = bin_data[data_start: data_end]

        tab_addr = ptr_table_addr.get_address()

        pointer_list.append({'ptr_table_hex': ptr_table_addr.pc_address, 'ptr_table_dec': tab_addr,
                             'index': ptr_index, 'length': data_end - data_start, 'pc': ptr.pc_address,
                             'lorom': ptr.lorom1_address, 'pc_dec': data_start})

        # add it to the list using the requested address type
        if data_start not in [b['id'] for b in bin_list]:
            this_id = f'${tab_addr}:{ptr_index}'
            if addr_type == 1:  # index only
                this_id = ptr_index
            elif addr_type == 2:  # the pc address of the pointer
                this_id = f'(${i})'
            elif addr_type == 3:  # pc address of the data block
                this_id = f'[${data_start}]'
            elif addr_type == 4:  # table address, index and data addr
                this_id += f'[${data_start}]'
            bin_list.append({'id': this_id, 'data': data})

        if data and len(data) > 1:  # if there was some data, spit it out
            file_name = f"./{table_folder}/{data_start}.bin"
            try:
                ptr_file = open(file_name, "wb")
                ptr_file.write(bytearray(data))
                ptr_file.close()
            except Exception as ex:
                print(f"Error: {repr(ex)}")
        ptr_index += 1
    if output:
        write_csv(table_folder, pointer_list)
        if table:  # if we get a table, output the text representation
            write_script(f'./{out_folder}/{table_name}.txt', bin_list, tbl)
            print(f'Saved {hex(ptr_index)}({ptr_index}) blocks of data from table: {table_name}.')

    ptr_data['bin_list'] = bin_list
    ptr_data['ptr_list'] = pointer_list
    return ptr_data


def write_script(filename: str, dict_data: list, tbl: Table):
    line1 = True
    nl = "\n"
    with open(filename, 'w', encoding=tbl.encoding) as of:
        for data in dict_data:
            of.write(f"{'' if line1 else nl}<<{data.get('id')}>>{nl}")
            of.write(tbl.interpret_binary_data(data['data']))
            line1 = False


def write_csv(filename, dict_data: list):
    import csv
    csv_columns = dict_data[0].keys()
    csv_file = f"./{filename}.csv"
    try:
        with open(csv_file, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=csv_columns)
            writer.writeheader()
            for data in dict_data:
                writer.writerow(data)
    except IOError:
        print("I/O error")
