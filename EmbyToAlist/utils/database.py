from tinydb import TinyDB, Query

class TinyDBHandler:
    def __init__(self, db_path='data.json', table_name='default'):
        """
        初始化 TinyDB，并选择默认表
        """
        self.db = TinyDB(db_path)
        self.set_table(table_name)

    def set_table(self, table_name: str):
        """
        切换当前操作的表
        """
        self.table = self.db.table(table_name)
        self.query = Query()

    def insert_one(self, data: dict):
        return self.table.insert(data)

    def insert_many(self, data_list: list):
        return self.table.insert_multiple(data_list)

    def get_all(self):
        return self.table.all()

    def search(self, condition):
        return self.table.search(condition(self.query))

    def update(self, fields: dict, condition):
        return self.table.update(fields, condition(self.query))

    def delete(self, condition):
        return self.table.remove(condition(self.query))

    def clear(self):
        self.table.truncate()
