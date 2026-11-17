package dev.notsg.cosmo

import android.app.NotificationManager
import android.content.Context
import android.net.Uri
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import kotlin.math.abs

data class Line(val who: String, val text: String)
data class Task(val id: String, val title: String)
data class Snapshot(
    val connected: Boolean = false,
    val state: String = "idle",
    val lines: List<Line> = emptyList(),
    val tasks: List<Task> = emptyList(),
    val approval: String? = null,
    val approvalId: String? = null,
)

data class Pairing(val hosts: List<String>, val port: Int, val token: String)

object Bridge {
    val live = MutableStateFlow(Snapshot())
    @Volatile private var working: String? = null
    @Volatile private var active: HttpURLConnection? = null
    @Volatile private var generation = 0
    @Volatile private var skew = 0L
    private val random = SecureRandom()
    private val framed = Regex("""\{"sig":"([0-9a-f]{64})","event":(.*)\}""")
    private val states = setOf("idle", "listening", "thinking", "speaking", "waiting_for_approval")

    fun parse(link: String): Pairing? {
        val uri = Uri.parse(link.trim())
        if (uri.scheme != "cosmo" || uri.host != "pair") return null
        val hosts = uri.getQueryParameter("host")?.split(",")?.map { it.trim() }?.filter { it.isNotEmpty() }.orEmpty()
        val port = uri.getQueryParameter("port")?.toIntOrNull() ?: return null
        val token = uri.getQueryParameter("token")?.takeIf { it.isNotBlank() } ?: return null
        return if (hosts.isEmpty()) null else Pairing(hosts, port, token)
    }

    fun load(context: Context): Pairing? {
        val prefs = context.getSharedPreferences("pair", Context.MODE_PRIVATE)
        return parse(prefs.getString("link", null) ?: return null)
    }

    fun save(context: Context, link: String?) {
        context.getSharedPreferences("pair", Context.MODE_PRIVATE).edit().putString("link", link).commit()
        working = null
        generation++
        live.value = Snapshot()
        context.getSystemService(NotificationManager::class.java).cancel(APPROVAL_ID)
        close()
    }

    private fun hex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it) }

    private fun mac(token: String, vararg parts: String): String {
        val mac = Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(token.toByteArray(), "HmacSHA256")) }
        return hex(mac.doFinal(parts.joinToString("\n").toByteArray()))
    }

    private fun sha(bytes: ByteArray) = hex(MessageDigest.getInstance("SHA-256").digest(bytes))

    private fun same(a: String?, b: String) = a != null && MessageDigest.isEqual(a.toByteArray(), b.toByteArray())

    private fun readAll(conn: HttpURLConnection) =((if (conn.responseCode < 400) conn.inputStream else conn.errorStream)?.use { it.readBytes() } ?: ByteArray(0))

    private fun verified(conn: HttpURLConnection, token: String, nonce: String, bytes: ByteArray) =
        same(conn.getHeaderField("X-Cosmo-Signature"), mac(token, "response", nonce, conn.responseCode.toString(), sha(bytes)))

    fun open(pairing: Pairing, path: String, method: String = "GET", body: JSONObject? = null, readTimeout: Int = 8000, connectTimeout: Int = 3000): Pair<HttpURLConnection, String> {
        var last: Exception? = null
        val order = listOfNotNull(working?.takeIf { it in pairing.hosts }) + pairing.hosts.filter { it != working }
        for (host in order) {
            var retried = false
            while (true) {
                var sent = false
                try {
                    val conn = URL("http://$host:${pairing.port}$path").openConnection() as HttpURLConnection
                    conn.connectTimeout = connectTimeout
                    conn.readTimeout = readTimeout
                    conn.requestMethod = method
                    val bytes = body?.toString()?.toByteArray() ?: ByteArray(0)
                    val stamp = (System.currentTimeMillis() / 1000 + skew).toString()
                    val nonce = hex(ByteArray(16).also { random.nextBytes(it) })
                    conn.setRequestProperty("X-Cosmo-Time", stamp)
                    conn.setRequestProperty("X-Cosmo-Nonce", nonce)
                    conn.setRequestProperty("X-Cosmo-Signature", mac(pairing.token, method, path, stamp, nonce, sha(bytes)))
                    if (body != null) {
                        conn.doOutput = true
                        conn.setRequestProperty("Content-Type", "application/json")
                        conn.outputStream.use { it.write(bytes) }
                        sent = true
                    }
                    if (conn.responseCode == 401) {
                        val reply = readAll(conn)
                        val trusted = verified(conn, pairing.token, nonce, reply)
                        conn.disconnect()
                        val now = if (!trusted) 0L else runCatching { JSONObject(String(reply)) }.getOrNull()?.takeIf { it.optString("error") == "clock" }?.optLong("now", 0L) ?: 0L
                        if (now > 0 && !retried) {
                            skew = now - System.currentTimeMillis() / 1000
                            retried = true
                            continue
                        }
                        throw IllegalStateException(if (now > 0) "Your phone's clock is off by about ${abs(now - System.currentTimeMillis() / 1000) / 60} minutes." else "The Mac rejected the pairing token. Pair again.")
                    }
                    working = host
                    return conn to nonce
                } catch (e: IllegalStateException) {
                    throw e
                } catch (e: Exception) {
                    if (sent) throw IllegalStateException("No reply from the Mac")
                    last = e
                    break
                }
            }
        }
        throw last ?: IllegalStateException("No address to try")
    }

    fun call(pairing: Pairing, path: String, method: String = "GET", body: JSONObject? = null, readTimeout: Int = 8000, connectTimeout: Int = 3000): Pair<Int, String> {
        val (conn, nonce) = open(pairing, path, method, body, readTimeout, connectTimeout)
        try {
            val bytes = readAll(conn)
            if (!verified(conn, pairing.token, nonce, bytes)) throw IllegalStateException("The Mac's reply failed its signature check.")
            return conn.responseCode to String(bytes)
        } finally {
            conn.disconnect()
        }
    }

    fun post(context: Context, path: String, body: JSONObject = JSONObject(), quick: Boolean = false): String? {
        val pairing = load(context) ?: return "Not paired"
        return try {
            val known = working?.takeIf { quick && it in pairing.hosts }
            val target = if (known != null) pairing.copy(hosts = listOf(known)) else pairing
            val (code, text) = if (quick) call(target, path, "POST", body, 2500, 2000) else call(target, path, "POST", body)
            if (code < 400) null else runCatching { JSONObject(text).optString("error") }.getOrNull()?.ifBlank { null } ?: "Error $code"
        } catch (e: Exception) {
            e.message ?: "Could not reach the Mac"
        }
    }

    fun event(token: String, nonce: String, seq: Int, data: String): JSONObject? {
        val (sig, raw) = framed.matchEntire(data)?.destructured ?: return null
        if (!same(sig, mac(token, "event", nonce, raw))) return null
        return JSONObject(raw).takeIf { it.optInt("seq", -1) == seq }
    }

    fun stream(context: Context) {
        val gen = generation
        val pairing = load(context) ?: return
        val (conn, nonce) = open(pairing, "/events", readTimeout = 45000)
        active = conn
        try {
            if (gen != generation) return
            if (conn.responseCode != 200) throw IllegalStateException("Stream failed: ${conn.responseCode}")
            live.update { it.copy(connected = true) }
            val reader: BufferedReader = conn.inputStream.bufferedReader()
            var seq = 0
            while (gen == generation) {
                val line = reader.readLine() ?: break
                if (!line.startsWith("data: ")) continue
                val event = event(pairing.token, nonce, seq++, line.removePrefix("data: ")) ?: throw IllegalStateException("An event failed its signature check.")
                if (gen == generation) apply(event)
            }
        } finally {
            if (gen == generation) live.update { it.copy(connected = false) }
            conn.disconnect()
        }
    }

    var onEvent: ((JSONObject) -> Unit)? = null

    fun close() {
        Thread { active?.disconnect() }.start()
    }

    private fun apply(event: JSONObject) {
        val kind = event.optString("state")
        onEvent?.invoke(event)
        if (kind == "snapshot") {
            val d = event.getJSONObject("detail")
            val lines = d.getJSONArray("conversation").let { a -> (0 until a.length()).map { a.getJSONObject(it) }.map { Line(it.getString("who"), it.getString("text")) } }
            val tasks = d.getJSONArray("tasks").let { a -> (0 until a.length()).map { a.getJSONObject(it) }.map { Task(it.optString("id"), it.optString("title")) } }
            val approval = if (d.isNull("approval")) null else d.getString("approval")
            val approvalId = if (approval == null || d.isNull("approval_id")) null else d.getString("approval_id")
            live.update { it.copy(state = d.getString("state"), lines = lines, tasks = tasks, approval = approval, approvalId = approvalId) }
        } else {
            live.update { s ->
                val detail = event.optString("detail")
                var next = if (kind in states) s.copy(state = kind) else s
                when (kind) {
                    "said", "heard" -> if (detail.isNotEmpty()) next = next.copy(lines = (next.lines + Line(if (kind == "said") "cosmo" else "you", detail)).takeLast(30))
                    "waiting_for_approval" -> next = next.copy(approval = detail.ifBlank { next.approval ?: "Cosmo needs your approval" }, approvalId = event.optString("id").ifBlank { null })
                    "approval" -> next = next.copy(approval = null, approvalId = null)
                    "worker_started" -> event.optJSONObject("detail")?.let { d -> next = next.copy(tasks = next.tasks + Task(d.optString("id"), d.optString("title"))) }
                    "worker_done" -> event.optJSONObject("detail")?.let { d -> next = next.copy(tasks = next.tasks.filter { it.id != d.optString("id") }) }
                }
                next
            }
        }
    }
}
