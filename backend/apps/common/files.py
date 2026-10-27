from django.db import transaction


def delete_files_after_commit(*files):
    """
    Remove stored files once the database change is committed, so a
    rollback never leaves a row pointing at a deleted file.
    """
    names = [(f.storage, f.name) for f in files if f]

    def delete():
        for storage, name in names:
            storage.delete(name)

    transaction.on_commit(delete)
