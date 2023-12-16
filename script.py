

class Table:
    def __init__(self, table_file):
        import collections
        enc = self.detect_encoding(table_file)
        val_map = {}
        char_map = {}
        err_count = 0
        with open(table_file, encoding=enc) as to:
            line = to.readline()
            cnt = 1
            while line:
                try:
                    line_data = line.split('=')
                    if len(line_data) == 2:
                        val = line_data[0]
                        ch = line_data[1]
                        ch = ch.replace('\n', '').replace('\r', '').replace('\\', '\n')

                        if self.exists(val, '**'):
                            for d in range(0, 256):
                                prep_ch = ch.replace('**', self.hex(d))
                                prep_val = val.replace('**', self.hex(d))
                                if self.exists(val, '%%'):
                                    for e in range(0, 256):
                                        ch_val = prep_ch.replace('%%', self.hex(e))
                                        val_val = prep_val.replace('%%', self.hex(e))

                                        dec_val = int(val_val, 16)

                                        if dec_val not in val_map.keys():
                                            val_map[dec_val] = ch_val
                                        if prep_ch not in char_map.keys():
                                            char_map[ch_val] = dec_val
                                else:
                                    dec_val = int(prep_val, 16)

                                    if dec_val not in val_map.keys():
                                        val_map[dec_val] = prep_ch
                                    if prep_ch not in char_map.keys():
                                        char_map[prep_ch] = dec_val
                        else:
                            dec_val = int(val, 16)

                            if dec_val not in val_map.keys():
                                val_map[dec_val] = ch
                            if ch not in char_map.keys():
                                char_map[ch] = dec_val
                except Exception as ex:
                    err_count += 1
                line = to.readline()
                cnt += 1
        self.__val_map = val_map
        self.__chr_map = char_map
        self.__errors = err_count
        self.__parsed_lines = cnt
        self.__file_name = table_file
        self.__encoding = enc

    @property
    def encoding(self):
        return self.__encoding

    @staticmethod
    def exists(str_val: str, search: str):
        try:
            str_val.index(search)
            return True
        except ValueError:
            return False

    def get_value(self, word: str, infer_value=True):
        if type(word) is not str:
            raise ValueError("Value must be a string!")
        if word in self.__chr_map.keys():
            return self.__chr_map[word]
        if infer_value and '[' in word and ']' in word:
            try:
                word = word.replace('[', '').replace(']', '')
                return int(word, 16)
            except:
                print("Warning: Value could not be determined.")
        return None

    def get_chars(self, value: int, return_hex_repr=True):
        if value in self.__val_map.keys():
            return self.__val_map[value]
        return f'[{self.hex(value)}]' if return_hex_repr else None

    @staticmethod
    def hex(value):
        if value < 0x100:
            pad = 2
        elif value < 0x10000:
            pad = 4
        elif value < 0x1000000:
            pad = 6
        elif value < 0x100000000:
            pad = 8
        else:
            raise ValueError("Error: Table Value is not supported!")
        return f'{value:0{pad}X}'

    @staticmethod
    def byte_size(value: int):
        """
        Get the number of bytes representing the value
        :param value: integer value
        :return: number of bytes
        """
        from math import log
        if value == 0:
            return 1
        return int(log(value, 256)) + 1

    @staticmethod
    def __get_byte_multiplier(value):
        if value == 0:
            return 1
        final = '1'
        for i in range(0, value):
            final += '00'
        return int(final, 16)

    @staticmethod
    def bytes_to_val(byte_list: list, reverse=False):
        final_val = 0
        if reverse:
            byte_list.reverse()
        for b in range(0, len(byte_list)):
            final_val |= (byte_list[b] << (b * 8))  # self.__get_byte_multiplier(b)
        return final_val

    def interpret_binary(self, input_filename, max_bytes=3):
        with open(input_filename, "rb") as data_file:
            bin_data = list(data_file.read())
        return self.interpret_binary_data(bin_data, max_bytes)

    def interpret_binary_data(self, bin_data, max_bytes=3):
        final_string = ''
        i = 0
        while i <= len(bin_data) + 1:
            len_check = max_bytes
            char = None
            found_char = False
            while len_check > 0:
                end_check = i + len_check
                val = self.bytes_to_val(bin_data[i: end_check], True)
                char = self.get_chars(val, False)
                if char:
                    found_char = True
                    i += (len_check - 1)
                    len_check = 0
                else:
                    len_check -= 1

            if not found_char:
                char = self.get_chars(bin_data[i], True)
            if char is None:
                print(f"ERROR - Unable to resolve byte ({hex(bin_data[i])})???")
            else:
                final_string += char
            i += 1
        return final_string

    def has_char(self, bin_data):
        """
        Check for a valid character using all given bytes
        :param bin_data: the list of bytes
        :return: if there is a valid character using these bytes
        """
        # get the value by OR'ing and shifting the bytes together
        val = self.bytes_to_val(bin_data)

        # check for valid value...
        if len(bin_data) > 1 and val in bin_data:
            # in the case of [00, 00] or [00, 90] or etc.
            # the total value cannot equal a single byte value
            return False

        # using the value, check for a defined character
        char = self.get_chars(val, False)

        return True if char else False

    def check_for_lone_byte(self, bin_data, index, value=0x0):
        """
        Used to check for the end of a text block
        :param bin_data: a list of values
        :param index: current list index
        :param value: check for this value
        :return: if the value is found,
        check to see if it is part of a larger value
        """
        start1 = index - 3
        start2 = index - 2
        end0 = index + 1
        end1 = index + 2
        end2 = index + 3
        if bin_data[index] == value:
            if self.has_char(bin_data[start1: end0]):
                return 0
            elif self.has_char(bin_data[start2: end0]):
                return 0
            elif self.has_char(bin_data[index: end1]):
                return 1
            elif self.has_char(bin_data[index: end2]):
                return 2
            else:
                return -1
        return 0

    @staticmethod
    def detect_encoding(file_path, lines=80):
        """
        Given a file, use the first X number of lines to detect the encoding
        :param file_path: path to text file
        :param lines: defaults to 80
        :return: the assumed file encoding using chardet
        """
        import chardet
        with open(file_path, 'rb') as f:
            raw_data = b''.join([f.readline() for _ in range(lines)])
        return chardet.detect(raw_data)['encoding']
