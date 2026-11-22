"""1.0: several computers keep one vault, and keep each other up to date.

    stamps.py     when each document, person, device or setting last changed, and where (the newest wins)
    records.py    a document, person or device as one computer sends it to another, and putting it in place
    transport.py  calling another computer: HTTPS, trusting only the certificate it was paired with
    service.py    pairing a computer that also keeps the vault, and catching up with each other in the background

Each computer pulls from the ones it was paired with every few seconds: it asks what changed, and copies whatever is
newer than its own (documents with their encrypted files and search data, family members, paired devices, family
sharing). A deletion is remembered, so the other computer doesn't bring the document back.
"""
