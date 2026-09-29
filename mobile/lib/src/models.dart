// Data shapes returned by the vault's API (backend/api/app.py).

const categories = ['insurance', 'warranty', 'bill', 'property', 'identity', 'other'];
const categoryLabels = {
  'insurance': 'Insurance',
  'warranty': 'Warranties',
  'bill': 'Bills',
  'property': 'Property',
  'identity': 'Identity',
  'other': 'Other',
};

/// Below this OCR confidence a scan is flagged as hard to read (backend/parser/ocr.py).
const lowConfidence = 70;

class Info {
  Info({required this.owner, required this.isAdmin, required this.numDocuments, required this.assistantReady,
      required this.assistantMessage, required this.ocrAvailable});

  final String owner;
  final bool isAdmin;
  final int numDocuments;
  final bool assistantReady;
  final String assistantMessage;
  final bool ocrAvailable;

  factory Info.fromJson(Map<String, dynamic> j) => Info(
        owner: j['owner'] as String,
        isAdmin: j['is_admin'] as bool? ?? false,
        numDocuments: j['num_documents'] as int? ?? 0,
        assistantReady: (j['llm_status'] as Map?)?['ready'] as bool? ?? false,
        assistantMessage: (j['llm_status'] as Map?)?['message'] as String? ?? '',
        ocrAvailable: j['ocr_available'] as bool? ?? false,
      );
}

class VaultDocument {
  VaultDocument({required this.id, required this.filename, required this.category, required this.pages,
      required this.ocrPages, required this.ocrConfidence, required this.sizeBytes, required this.addedAt,
      this.needsReview = false, this.expiresOn, this.dueOn, this.amount, this.reference});

  final String id;
  final String filename;
  final String category;
  final int pages;
  final int ocrPages;
  final int? ocrConfidence;
  final int sizeBytes;
  final DateTime addedAt;

  /// Sorted automatically, but the vault wasn't sure of the type (Phase 8).
  final bool needsReview;

  /// Key facts read from the document (Phase 9).
  final DateTime? expiresOn;
  final DateTime? dueOn;
  final String? amount;
  final String? reference;

  bool get hardToRead => ocrConfidence != null && ocrConfidence! < lowConfidence;

  factory VaultDocument.fromJson(Map<String, dynamic> j) => VaultDocument(
        id: j['doc_id'] as String,
        filename: j['filename'] as String,
        category: j['category'] as String,
        pages: j['pages'] as int? ?? 1,
        ocrPages: j['ocr_pages'] as int? ?? 0,
        ocrConfidence: j['ocr_confidence'] as int?,
        sizeBytes: j['size_bytes'] as int? ?? 0,
        addedAt: DateTime.tryParse(j['added_at'] as String? ?? '') ?? DateTime.now(),
        needsReview: j['needs_review'] as bool? ?? false,
        expiresOn: DateTime.tryParse(j['expires_on'] as String? ?? ''),
        dueOn: DateTime.tryParse(j['due_on'] as String? ?? ''),
        amount: j['amount'] as String?,
        reference: j['reference'] as String?,
      );
}

class Source {
  Source({required this.rank, required this.chunkId, required this.docId, required this.filename,
      required this.category, required this.page, required this.text});

  final int rank;
  final String chunkId;
  final String docId;
  final String filename;
  final String category;
  final int page;
  final String text;

  factory Source.fromJson(Map<String, dynamic> j) => Source(
        rank: j['rank'] as int,
        chunkId: j['chunk_id'] as String,
        docId: j['doc_id'] as String,
        filename: j['filename'] as String,
        category: j['category'] as String,
        page: j['page'] as int,
        text: j['text'] as String,
      );

  /// A document opened from the list: its first page, no passage to highlight.
  factory Source.firstPage(VaultDocument d) => Source(
      rank: 0, chunkId: '', docId: d.id, filename: d.filename, category: d.category, page: 1, text: '');
}

class Highlights {
  Highlights({required this.kind, required this.page, required this.pages, required this.width, required this.height,
      required this.passage, required this.facts, required this.factsFound, required this.passageFound,
      required this.scanned, required this.text, required this.confidence});

  /// "pdf" (boxes on a page image), "scan" (boxes on a straightened photo/scan),
  /// "text" (character spans in [text]) or "image" (a scan the Mac can't read: no boxes).
  final String kind;
  final int page;
  final int pages;
  final double width;
  final double height;
  final List<List<double>> passage;
  final List<List<double>> facts;
  final List<String> factsFound;
  final bool passageFound;
  final bool scanned;
  final String? text;
  final int? confidence;

  static List<List<double>> _boxes(Object? v) =>
      ((v as List?) ?? []).map((b) => (b as List).map((n) => (n as num).toDouble()).toList()).toList();

  factory Highlights.fromJson(Map<String, dynamic> j) => Highlights(
        kind: j['kind'] as String,
        page: j['page'] as int,
        pages: j['pages'] as int,
        width: (j['width'] as num? ?? 0).toDouble(),
        height: (j['height'] as num? ?? 0).toDouble(),
        passage: _boxes(j['passage']),
        facts: _boxes(j['facts']),
        factsFound: ((j['facts_found'] as List?) ?? []).cast<String>(),
        passageFound: j['passage_found'] as bool? ?? false,
        scanned: j['scanned'] as bool? ?? false,
        text: j['text'] as String?,
        confidence: j['confidence'] as int?,
      );
}

class UploadResult {
  UploadResult({required this.filename, required this.status, this.document, this.error, this.code});

  final String filename;
  final String status; // added | duplicate | error
  final VaultDocument? document;
  final String? error;

  /// "password_required" / "wrong_password": a locked PDF (e-Aadhaar, e-PAN): ask for its password.
  final String? code;

  factory UploadResult.fromJson(Map<String, dynamic> j) => UploadResult(
        filename: j['filename'] as String,
        status: j['status'] as String,
        document: j['document'] == null ? null : VaultDocument.fromJson(j['document'] as Map<String, dynamic>),
        error: j['error'] as String?,
        code: j['code'] as String?,
      );
}

/// One line of the /api/chat stream.
sealed class ChatEvent {
  static ChatEvent fromJson(Map<String, dynamic> j) => switch (j['type']) {
        'sources' => SourcesEvent(((j['sources'] as List?) ?? []).map((s) => Source.fromJson(s as Map<String, dynamic>)).toList()),
        'token' => TokenEvent(j['text'] as String? ?? ''),
        'done' => DoneEvent(found: j['found'] as bool? ?? true),
        _ => ErrorEvent(j['message'] as String? ?? 'Something went wrong.'),
      };
}

class SourcesEvent extends ChatEvent {
  SourcesEvent(this.sources);
  final List<Source> sources;
}

class TokenEvent extends ChatEvent {
  TokenEvent(this.text);
  final String text;
}

class DoneEvent extends ChatEvent {
  DoneEvent({required this.found});
  final bool found;
}

class ErrorEvent extends ChatEvent {
  ErrorEvent(this.message);
  final String message;
}

/// An Aadhaar number shown on screen keeps only its last four digits.
String showReference(String ref) =>
    RegExp(r'^\d{4} \d{4} \d{4}$').hasMatch(ref) ? 'XXXX XXXX ${ref.substring(ref.length - 4)}' : ref;
