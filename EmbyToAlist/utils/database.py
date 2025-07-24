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
        self.set_table(table_name)

    def get_all_tables(self) -> List[str]:
        """
        获取所有表名
        :return: 表名列表
        """
        return self.db.tables()
    
    def set_table(self, table_name: str) -> None:
        """
        切换当前操作的表
        """
        self.table = self.db.table(table_name)

    def insert_one(self, data: Dict[str, Any], table_name: Optional[str] = None) -> int:
        """
        插入一条记录
        :param data: 字典形式的数据
        :return: 新插入记录的 doc_id
        """
        if table_name:
            table = self.db.table(table_name)
            return table.insert(data)
        else:
            return self.table.insert(data)

    def insert_many(self, data_list: List[Dict[str, Any]]) -> List[int]:
        """
        插入多条记录
        :param data_list: 字典列表
        :return: 新插入记录的 doc_id 列表
        """
        return self.table.insert_multiple(data_list)

    def get_all(self, table_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        获取当前表所有记录
        :return: 记录列表
        """
        if table_name:
            table = self.db.table(table_name)
            return table.all()
        else:
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
            return self.table.search(condition(Query()))

    def update(
        self, 
        fields: Union[Dict[str, Any], Callable[[Dict], Any]], 
        condition: Callable[[QueryInstance], Any]
    ) -> List[int]:
        """
        更新匹配条件的记录
        :param fields: 要更新的字段和值, or a function that takes a doc and returns a dict of updates
        :param condition: 查询条件
        :return: 更新的 doc_id 列表
        """
        _fields = fields
        if callable(fields):
            def updater(doc):
                updates = fields(doc)
                if updates and isinstance(updates, dict):
                    doc.update(updates)
            _fields = updater
        
        return self.table.update(_fields, condition(Query()))

    def delete(
        self, 
        condition: Callable[[QueryInstance], Any]
    ) -> List[int]:
        """
        删除匹配条件的记录
        :param condition: 查询条件
        :return: 删除的 doc_id 列表
        """
        return self.table.remove(condition(Query()))

    def clear(self) -> None:
        """
        清空当前表所有记录
        """
        self.table.truncate()

    def increment(
        self,
        condition: Callable[[QueryInstance], Any],
        field: str,
        step: int = 1,
        table_name: Optional[str] = None,
    ) -> None:
        """
        计数递增：如果记录存在则字段递增，不存在则插入默认数据(step为默认值)
        
        :param condition: 查询条件，接收 Query 对象
        :param field: 要递增的字段名
        :param step: 递增步长，默认+1
        :param table_name: 可选，指定表名
        """
        target_table = self.db.table(table_name) if table_name else self.table

        def updater(doc):
            doc[field] = doc.get(field, 0) + step

        # 使用 update 方法，如果更新成功（返回非空列表），说明记录存在
        updated_ids = target_table.update(updater, condition(Query()))

        # 如果没有记录被更新，说明记录不存在，需要插入
        if not updated_ids:
            default_data = {field: step}
            # 这里需要额外的数据来满足 condition，但当前设计无法通用获取
            # 暂时只插入 field 和 step，这可能导致查询条件在未来失效
            # 更好的设计是确保初始化时所有可能的记录都已存在
            target_table.insert(default_data)