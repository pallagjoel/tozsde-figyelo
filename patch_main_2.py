import re

def patch_main_py_2():
    with open('main.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # Database API changes
    content = content.replace('get_tracked_stocks()', 'get_tracked_stocks(current_user.id)')
    content = content.replace('add_tracked_stock(', 'add_tracked_stock(current_user.id, ')
    content = content.replace('remove_tracked_stock(ticker)', 'remove_tracked_stock(current_user.id, ticker)')
    content = content.replace('stock_exists(ticker)', 'stock_exists(current_user.id, ticker)')
    content = content.replace('stock_exists(t)', 'stock_exists(current_user.id, t)')
    content = content.replace('remove_tracked_stock(t)', 'remove_tracked_stock(current_user.id, t)')

    # CustomObject filtering
    content = content.replace('session.query(CustomObject).all()', 'session.query(CustomObject).filter(CustomObject.user_id == current_user.id).all()')
    content = content.replace('session.query(CustomObject).filter(CustomObject.name == obj.name).first()', 'session.query(CustomObject).filter(CustomObject.name == obj.name, CustomObject.user_id == current_user.id).first()')
    content = content.replace('session.query(CustomObject).filter(CustomObject.id == object_id).first()', 'session.query(CustomObject).filter(CustomObject.id == object_id, CustomObject.user_id == current_user.id).first()')
    content = content.replace('session.query(CustomObject).filter(CustomObject.name == object_name).first()', 'session.query(CustomObject).filter(CustomObject.name == object_name, CustomObject.user_id == current_user.id).first()')
    
    # CustomField filtering
    content = content.replace('session.query(CustomField).all()', 'session.query(CustomField).filter(CustomField.user_id == current_user.id).all()')
    # For /api/admin/fields with object_id
    content = content.replace('query = session.query(CustomField)', 'query = session.query(CustomField).filter(CustomField.user_id == current_user.id)')
    
    # CustomField uniqueness checks
    content = content.replace('session.query(CustomField).filter(CustomField.object_id == field.object_id, CustomField.name == field.name).first()', 'session.query(CustomField).filter(CustomField.object_id == field.object_id, CustomField.name == field.name, CustomField.user_id == current_user.id).first()')
    content = content.replace('session.query(CustomField).filter(CustomField.id == field_id).first()', 'session.query(CustomField).filter(CustomField.id == field_id, CustomField.user_id == current_user.id).first()')

    # CustomRecord filtering
    content = content.replace('session.query(CustomRecord).filter(CustomRecord.object_id == obj.id)', 'session.query(CustomRecord).filter(CustomRecord.object_id == obj.id, CustomRecord.user_id == current_user.id)')
    content = content.replace('session.query(CustomRecord).filter(CustomRecord.id == record_id).first()', 'session.query(CustomRecord).filter(CustomRecord.id == record_id, CustomRecord.user_id == current_user.id).first()')

    # Fix insertions to inject user_id
    content = content.replace('new_obj = CustomObject(', 'new_obj = CustomObject(user_id=current_user.id, ')
    content = content.replace('new_field = CustomField(', 'new_field = CustomField(user_id=current_user.id, ')
    content = content.replace('new_record = CustomRecord(', 'new_record = CustomRecord(user_id=current_user.id, ')

    # API Records list (for Stocks: object_id IS NULL)
    content = content.replace('session.query(CustomField).filter(\n            CustomField.object_id == None, CustomField.is_active == True\n        )', 'session.query(CustomField).filter(CustomField.user_id == current_user.id, CustomField.object_id == None, CustomField.is_active == True)')
    content = content.replace('session.query(CustomField).filter(CustomField.object_id == None, CustomField.is_active == True)', 'session.query(CustomField).filter(CustomField.user_id == current_user.id, CustomField.object_id == None, CustomField.is_active == True)')

    with open('main.py', 'w', encoding='utf-8') as f:
        f.write(content)
        
    print("Patched main.py logic filters!")

if __name__ == "__main__":
    patch_main_py_2()
