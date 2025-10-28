"""
Mock Supabase client for test environments
"""
from typing import Any, Dict, List, Optional


class MockExecuteResponse:
    """Mock response from Supabase execute()"""
    def __init__(self, data=None, count=None):
        self.data = data if data is not None else []
        self.count = count


class MockQueryBuilder:
    """Mock Supabase query builder"""

    def __init__(self, table_name: str, data: List[Dict[str, Any]] = None):
        self.table_name = table_name
        self.data = data if data is not None else []
        self.filters = []

    def select(self, columns: str = "*", **kwargs):
        """Mock select"""
        return self

    def insert(self, data: Dict[str, Any], **kwargs):
        """Mock insert"""
        # Add auto-generated id if not present
        if 'id' not in data:
            data['id'] = len(self.data) + 1
        self.data.append(data)
        return self

    def update(self, data: Dict[str, Any], **kwargs):
        """Mock update"""
        return self

    def delete(self, **kwargs):
        """Mock delete"""
        return self

    def eq(self, column: str, value: Any):
        """Mock eq filter"""
        self.filters.append(('eq', column, value))
        return self

    def neq(self, column: str, value: Any):
        """Mock neq filter"""
        self.filters.append(('neq', column, value))
        return self

    def gt(self, column: str, value: Any):
        """Mock gt filter"""
        return self

    def gte(self, column: str, value: Any):
        """Mock gte filter"""
        return self

    def lt(self, column: str, value: Any):
        """Mock lt filter"""
        return self

    def lte(self, column: str, value: Any):
        """Mock lte filter"""
        return self

    def like(self, column: str, value: str):
        """Mock like filter"""
        return self

    def ilike(self, column: str, value: str):
        """Mock ilike filter"""
        return self

    def is_(self, column: str, value: Any):
        """Mock is filter"""
        return self

    def in_(self, column: str, values: List[Any]):
        """Mock in filter"""
        return self

    def order(self, column: str, **kwargs):
        """Mock order"""
        return self

    def limit(self, count: int):
        """Mock limit"""
        return self

    def offset(self, count: int):
        """Mock offset"""
        return self

    def single(self):
        """Mock single"""
        return self

    def execute(self):
        """Mock execute - return mock response"""
        # For test environment, return data based on operation
        # If we have filters, apply them (simple eq filter support)
        result_data = self.data
        for filter_type, column, value in self.filters:
            if filter_type == 'eq':
                result_data = [d for d in result_data if d.get(column) == value]

        # Return the filtered or full data
        return MockExecuteResponse(data=result_data)

    def count(self):
        """Mock count"""
        return self


class MockSupabaseClient:
    """Mock Supabase client for test environments"""

    def __init__(self, supabase_url: str, supabase_key: str):
        self.supabase_url = supabase_url
        self.supabase_key = supabase_key
        self._tables = {}

    def table(self, table_name: str) -> MockQueryBuilder:
        """Return a mock query builder for the table"""
        if table_name not in self._tables:
            self._tables[table_name] = []
        return MockQueryBuilder(table_name, self._tables[table_name])

    def auth(self):
        """Mock auth - not implemented for tests"""
        return self

    def storage(self):
        """Mock storage - not implemented for tests"""
        return self

    def functions(self):
        """Mock functions - not implemented for tests"""
        return self

    def realtime(self):
        """Mock realtime - not implemented for tests"""
        return self