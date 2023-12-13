from snes import SFCAddress, SFCAddressType
from script import Table

"""
Little Master 3
TODO: Find more pointer locations.
      Work on script. Work on font.
      VWF?
"""


def extract_script_bins(file_name='2015.sfc', folder_prefix='en_old'):
    folder_name = f'{folder_prefix}_ptr_data'
    table_filename = 'jap.tbl'

    # 0x1456B - Unit Attribute Value Pointer (0x3 length) - 2 byte height, weight, 1 byte age
    # 0x1457F - Unit Weapon Name Pointer - Preceeding byte is entry length
    # 0x14588 - Unit Armor Name Pointer - Preceeding byte is entry length

    # unit and terrain descriptions
    extract_pointer_data(file_name, 0x30000, 0x500, 'unit-terrain-desc', folder_name, table=table_filename)

    # main script data
    extract_pointer_data(file_name, 0x1B0000, 0x400, 'script', folder_name, table=table_filename)

    # data for unit attacks that are re-used between the 5 unit type tables
    atk_data = extract_pointer_data(file_name, 0x1B0800, 0x6A, 'unit-attacks', folder_name,
                                    output=False, table=table_filename)
    atk_data = extract_pointer_data(file_name, 0x1B0A00, 0x6A, 'unit-attacks', folder_name, atk_data,
                                    False, table=table_filename)
    atk_data = extract_pointer_data(file_name, 0x1B0C00, 0x6A, 'unit-attacks', folder_name, atk_data,
                                    False, table=table_filename)
    atk_data = extract_pointer_data(file_name, 0x1B0E00, 0x6A, 'unit-attacks', folder_name, atk_data,
                                    False, table=table_filename)
    extract_pointer_data(file_name, 0x1B1000, 0x6A, 'unit-attacks', folder_name, atk_data, table=table_filename)

    # Scenario descriptions
    extract_pointer_data(file_name, 0x111EE3, 0x13C, 'scenario-desc', folder_name, table=table_filename)


def extract_pointer_data(input_filename: str, ptr_tbl_pos: int, tbl_len: int, table_name: str, out_folder='out',
                         ptr_data: dict = None, output=True, table: str = None):
    data_file = open(input_filename, "rb")
    bin_data = list(data_file.read())
    return pointer_extract(table_name, out_folder, bin_data, ptr_tbl_pos, tbl_len,
                           ptr_data=ptr_data, output=output, table=table)

@staticmethod
def create_folder_if_not_exists(folder_path):
    import os
    try:
        if not os.path.isdir(folder_path):
            os.mkdir(folder_path)
            print(f'Info: Created folder. "{folder_path}"')
    except OSError as error:
        print(f'Warning: Cannot create folder. "{folder_path}"')


def pointer_extract(table_name: str, out_folder: str, bin_data: list, ptr_tbl_loc: int, ptr_tbl_len: int = None,
                    ptr_bytes: int = 2, ptr_bank: int = None, ptr_data: dict = None, output=True, table: str = None):

    # if None or 0 is given, we will default
    if not ptr_tbl_len:
        ptr_tbl_len = 0x1000

    # create a SFCAddress object.
    # This allows us to make address mapping conversions a snap.
    ptr_table_addr = SFCAddress(ptr_tbl_loc)
    if not ptr_bank:
        ptr_bank = ptr_table_addr.get_bank_byte(SFCAddressType.LOROM1)
    if not ptr_data:
        ptr_data = {}
    tbl = Table(table) if table else None

    create_folder_if_not_exists(out_folder)

    table_folder = f'{out_folder}/{table_name}'
    create_folder_if_not_exists(table_folder)

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

        pointer_list.append({'ptr_table_hex': ptr_table_addr.pc_address, 'ptr_table_dec': ptr_table_addr.get_address(),
                             'index': ptr_index, 'length': data_end - data_start, 'pc': ptr.pc_address,
                             'lorom': ptr.lorom1_address, 'pc_dec': data_start})

        # add it to the list
        if data_start not in [b['id'] for b in bin_list]:
            bin_list.append({'id': data_start, 'data': data})

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


def write_script(filename: str, dict_data: list, tbl: Table, nl: str = "\n"):
    line1 = True
    with open(filename, 'w', encoding=tbl.encoding) as of:
        for data in dict_data:
            of.write(f"{'' if line1 else nl}<<${data['id']}>>{nl}")
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
