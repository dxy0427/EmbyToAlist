from typing import Callable, Dict, List, Optional, Any, Union
from tinydb import TinyDB, Query
from tinydb.table import Table
from tinydb.queries import QueryInstance

class TinyDBHandler:
    def __init__(self, db_path: str = 'data.json', table_name: str = 'default') -> None:
        """
        初始化 TinyDB，并选择默认表
        """
        self.db: TinyDB = TinyDB(db_path)
        self.table: Table
        self.query: QueryInstance
        self.set_table(table_name)

    def set_table(self, table_name: str) -> None:
        """
        切换当前操作的表
        """
        self.table = self.db.table(table_name)
        self.query = Query()

    def insert_one(self, data: Dict[str, Any]) -> int:
        """
        插入一条记录
        :param data: 字典形式的数据
        :return: 新插入记录的 doc_id
        """
        return self.table.insert(data)

    def insert_many(self, data_list: List[Dict[str, Any]]) -> List[int]:
        """
        插入多条记录
        :param data_list: 字典列表
        :return: 新插入记录的 doc_id 列表
        """
        return self.table.insert_multiple(data_list)

    def get_all(self) -> List[Dict[str, Any]]:
        """
        获取当前表所有记录
        :return: 记录列表
        """
        return self.table.all()

    def search(
        self, 
        condition: Callable[[QueryInstance], Any], 
        table_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        搜索记录
        :param condition: 查询条件，接收 Query 对象返回条件表达式
        :param table_name: 可选，指定表名
        :return: 匹配记录列表
        """
        if table_name:
            table = self.db.table(table_name)
            return table.search(condition(Query()))
        else:
            return self.table.search(condition(self.query))

    def update(
        self, 
        fields: Dict[str, Any], 
        condition: Callable[[QueryInstance], Any]
    ) -> List[int]:
        """
        更新匹配条件的记录
        :param fields: 要更新的字段和值
        :param condition: 查询条件
        :return: 更新的 doc_id 列表
        """
        return self.table.update(fields, condition(self.query))

    def delete(
        self, 
        condition: Callable[[QueryInstance], Any]
    ) -> List[int]:
        """
        删除匹配条件的记录
        :param condition: 查询条件
        :return: 删除的 doc_id 列表
        """
        return self.table.remove(condition(self.query))

    def clear(self) -> None:
        """
        清空当前表所有记录
        """
        self.table.truncate()
