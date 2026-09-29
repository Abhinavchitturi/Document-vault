package app.documentvault.document_vault

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.OpenableColumns
import android.webkit.MimeTypeMap
import io.flutter.embedding.android.FlutterFragmentActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File

// FlutterFragmentActivity (not FlutterActivity): the fingerprint prompt (local_auth) needs it.
//
// "Send to → Document Vault" from WhatsApp, Files, Gmail…: the shared files are copied into this
// app's private cache and handed to the app over the "app.documentvault/share" channel
// (lib/src/share_inbox.dart), which uploads them like a scan.
class MainActivity : FlutterFragmentActivity() {
    private var channel: MethodChannel? = null
    private var pending: List<Map<String, String>> = emptyList()

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val ch = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "app.documentvault/share")
        ch.setMethodCallHandler { call, result ->
            if (call.method == "takeShared") {
                result.success(pending)
                pending = emptyList()
            } else {
                result.notImplemented()
            }
        }
        channel = ch
        pending = sharedFiles(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val files = sharedFiles(intent)
        if (files.isNotEmpty()) {
            pending = pending + files
            channel?.invokeMethod("shared", null) // the app then asks for them with takeShared
        }
    }

    private fun sharedFiles(intent: Intent?): List<Map<String, String>> {
        if (intent == null) return emptyList()
        val uris: List<Uri> = when (intent.action) {
            Intent.ACTION_SEND -> listOfNotNull(streamExtra(intent))
            Intent.ACTION_SEND_MULTIPLE -> streamList(intent)
            else -> emptyList()
        }
        if (uris.isNotEmpty()) intent.action = Intent.ACTION_MAIN // handled: not again if the activity restarts
        return uris.mapNotNull { copyToCache(it) }
    }

    @Suppress("DEPRECATION")
    private fun streamExtra(intent: Intent): Uri? =
        if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        else intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)

    @Suppress("DEPRECATION")
    private fun streamList(intent: Intent): List<Uri> {
        val list: ArrayList<Uri>? =
            if (Build.VERSION.SDK_INT >= 33) intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
            else intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)
        return list ?: emptyList()
    }

    private fun copyToCache(uri: Uri): Map<String, String>? {
        return try {
            var name = "Shared document"
            contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                val i = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (i >= 0 && c.moveToFirst()) c.getString(i)?.let { name = it }
            }
            if (!name.contains('.')) { // WhatsApp and others may share without an extension: add it from the type
                val ext = MimeTypeMap.getSingleton().getExtensionFromMimeType(contentResolver.getType(uri) ?: "")
                if (ext != null) name = "$name.$ext"
            }
            val folder = File(cacheDir, "shared")
            folder.mkdirs()
            val out = File(folder, "${System.nanoTime()}-${name.replace('/', '_').replace('\\', '_')}")
            val input = contentResolver.openInputStream(uri) ?: return null
            input.use { src -> out.outputStream().use { dst -> src.copyTo(dst) } }
            mapOf("path" to out.absolutePath, "name" to name)
        } catch (e: Exception) {
            null
        }
    }
}
