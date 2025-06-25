

class BaseTest:

    # Custom assert methods implementation
    def assertIsNotNone(self, value, msg=None):
        """Assert that value is not None"""
        if value is None:
            raise AssertionError(msg or f"Expected not None, but got None")
    
    def assertEqual(self, first, second, msg=None):
        """Assert that first equals second"""
        if first != second:
            raise AssertionError(msg or f"Expected {first} == {second}, but {first} != {second}")
    
    def assertTrue(self, expr, msg=None):
        """Assert that expr is True"""
        if not expr:
            raise AssertionError(msg or f"Expected True, but got {expr}")
    
    def assertFalse(self, expr, msg=None):
        """Assert that expr is False"""
        if expr:
            raise AssertionError(msg or f"Expected False, but got {expr}")
    
    def assertAlmostEqual(self, first, second, places=7, msg=None):
        """Assert that first and second are approximately equal"""
        if round(abs(second - first), places) != 0:
            raise AssertionError(msg or f"Expected {first} ~= {second} (within {places} decimal places)")
    
    def assertIs(self, first, second, msg=None):
        """Assert that first is second (same object identity)"""
        if first is not second:
            raise AssertionError(msg or f"Expected {first} is {second}, but they are different objects")
    
    def assertIn(self, member, container, msg=None):
        """Assert that member is in container"""
        if member not in container:
            raise AssertionError(msg or f"Expected {member} in {container}")
    
    def assertIsInstance(self, obj, cls, msg=None):
        """Assert that obj is an instance of cls"""
        if not isinstance(obj, cls):
            raise AssertionError(msg or f"Expected {obj} to be instance of {cls}, but got {type(obj)}")
    
    def assertIsNone(self, value, msg=None):
        """Assert that value is None"""
        if value is not None:
            raise AssertionError(msg or f"Expected None, but got {value}")
    
    def assertNotEqual(self, first, second, msg=None):
        """Assert that first does not equal second"""
        if first == second:
            raise AssertionError(msg or f"Expected {first} != {second}, but they are equal")
    
    def assertGreater(self, first, second, msg=None):
        """Assert that first is greater than second"""
        if not first > second:
            raise AssertionError(msg or f"Expected {first} > {second}")
    
    def assertLess(self, first, second, msg=None):
        """Assert that first is less than second"""
        if not first < second:
            raise AssertionError(msg or f"Expected {first} < {second}")
    
    def assertGreaterEqual(self, first, second, msg=None):
        """Assert that first is greater than or equal to second"""
        if not first >= second:
            raise AssertionError(msg or f"Expected {first} >= {second}")
    
    def assertLessEqual(self, first, second, msg=None):
        """Assert that first is less than or equal to second"""
        if not first <= second:
            raise AssertionError(msg or f"Expected {first} <= {second}")
    
    def assertNotIn(self, member, container, msg=None):
        """Assert that member is not in container"""
        if member in container:
            raise AssertionError(msg or f"Expected {member} not in {container}")
    
    def assertIsNot(self, first, second, msg=None):
        """Assert that first is not second (different object identity)"""
        if first is second:
            raise AssertionError(msg or f"Expected {first} is not {second}, but they are the same object")
    
    def assertRaises(self, exception_class, callable_obj=None, *args, **kwargs):
        """Assert that calling callable_obj raises exception_class"""
        if callable_obj is None:
            # Return a context manager for use with 'with' statement
            return self._AssertRaisesContext(exception_class)
        else:
            try:
                callable_obj(*args, **kwargs)
                raise AssertionError(f"Expected {exception_class.__name__} to be raised, but no exception was raised")
            except exception_class:
                pass  # Expected exception was raised
            except Exception as e:
                raise AssertionError(f"Expected {exception_class.__name__} to be raised, but got {type(e).__name__}: {e}")
    
