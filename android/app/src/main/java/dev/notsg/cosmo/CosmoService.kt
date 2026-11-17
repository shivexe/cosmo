package dev.notsg.cosmo

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.ForegroundServiceStartNotAllowedException
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import org.json.JSONObject

private const val LINK_ID = 1
const val APPROVAL_ID = 2

class CosmoService : Service() {
    companion object {
        @Volatile var shown: String? = null
    }

    @Volatile private var running = false
    private var worker: Thread? = null
    private var keeper: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel("link", "Connection", NotificationManager.IMPORTANCE_MIN))
        nm.createNotificationChannel(NotificationChannel("approvals", "Approvals", NotificationManager.IMPORTANCE_HIGH))
        nm.createNotificationChannel(NotificationChannel("tasks", "Finished tasks", NotificationManager.IMPORTANCE_DEFAULT))
        nm.createNotificationChannel(NotificationChannel("using", "Cosmo using the phone", NotificationManager.IMPORTANCE_LOW))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val note = base("link").setContentTitle("Cosmo is linked").setOngoing(true).build()
        try {
            if (Build.VERSION.SDK_INT >= 34) startForeground(LINK_ID, note, ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING)
            else startForeground(LINK_ID, note)
        } catch (e: IllegalStateException) {
            if (Build.VERSION.SDK_INT >= 31 && e !is ForegroundServiceStartNotAllowedException) throw e
            stopSelf()
            return START_NOT_STICKY
        }
        if (!running) {
            running = true
            Bridge.onEvent = ::notify
            worker = Thread {
                while (running) {
                    if (Bridge.load(this) == null) break
                    try {
                        Bridge.stream(this)
                    } catch (_: Exception) {
                    }
                    try {
                        Thread.sleep(3000)
                    } catch (_: InterruptedException) {
                    }
                }
                stopSelf()
            }.also { it.start() }
            keeper = Thread {
                var sent = ""
                var at = 0L
                while (running) {
                    Wireless.idleOff(this)
                    if (Wireless.on(this)) Wireless.watch(this)
                    val endpoint = if (Wireless.on(this)) Wireless.endpoint else ""
                    val now = System.currentTimeMillis()
                    if (endpoint.isNotEmpty() && (endpoint != sent || now - at > 120000)) {
                        if (Bridge.post(this, "/adb", JSONObject().put("endpoint", endpoint)) == null) { sent = endpoint; at = now }
                    }
                    try { Thread.sleep(if (System.currentTimeMillis() - Wireless.lastUse < 30000 && endpoint.isEmpty()) 2000 else 15000) } catch (_: InterruptedException) { }
                }
            }.also { it.start() }
        } else {
            worker?.interrupt()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        Bridge.onEvent = null
        worker?.interrupt()
        keeper?.interrupt()
        Wireless.stop(this)
        Wireless.idleOff(this, now = true)
        Bridge.close()
        super.onDestroy()
    }

    private fun base(channel: String) = base(this, channel)

    private fun showApproval(id: String?, question: String?) {
        val nm = getSystemService(NotificationManager::class.java)
        if (id == null || question == null) {
            shown = null
            nm.cancel(APPROVAL_ID)
            return
        }
        if (id == shown) return
        shown = id
        nm.notify(APPROVAL_ID, approval(this, id, question, null))
    }

    private fun notify(event: JSONObject) {
        val nm = getSystemService(NotificationManager::class.java)
        when (event.optString("state")) {
            "snapshot" -> {
                val d = event.optJSONObject("detail") ?: return
                showApproval(if (d.isNull("approval_id")) null else d.optString("approval_id"), if (d.isNull("approval")) null else d.optString("approval"))
            }
            "waiting_for_approval" -> showApproval(event.optString("id").ifBlank { null }, event.optString("detail").ifBlank { Bridge.live.value.approval ?: "Cosmo needs your approval" })
            "approval" -> showApproval(null, null)
            "app_action" -> {
                if (event.optJSONObject("detail")?.optString("device") == "phone") Wireless.used(this)
                usingPhone(this, event)
            }
            "phone_adb" -> {
                Wireless.turnOn(this)
                keeper?.interrupt()
            }
            "interrupted" -> nm.cancel(USING_ID)
            "worker_done" -> {
                val d = event.optJSONObject("detail") ?: return
                val title = Bridge.live.value.tasks.firstOrNull { it.id == d.optString("id") }?.title ?: "Task finished"
                val summary = d.optString("summary").ifBlank { d.optString("status") }
                nm.notify(d.optString("id").hashCode(), base("tasks")
                    .setContentTitle(title)
                    .setContentText(summary)
                    .setStyle(Notification.BigTextStyle().bigText(summary))
                    .setAutoCancel(true)
                    .build())
            }
        }
    }
}

private fun base(context: Context, channel: String) = Notification.Builder(context, channel)
    .setSmallIcon(R.drawable.ic_cosmo)
    .setContentIntent(PendingIntent.getActivity(context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE))

private fun answer(context: Context, value: String, id: String, question: String, code: Int): Notification.Action {
    val intent = Intent(context, ApproveReceiver::class.java).putExtra("answer", value).putExtra("id", id).putExtra("question", question)
    val pending = PendingIntent.getBroadcast(context, code, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    return Notification.Action.Builder(null, if (value == "yes") "Allow" else "Deny", pending).build()
}

private fun approval(context: Context, id: String, question: String, error: String?): Notification {
    val text = if (error == null) question else "Couldn't send, tap again ($error)\n$question"
    return base(context, "approvals")
        .setContentTitle("Cosmo wants to")
        .setContentText(text)
        .setStyle(Notification.BigTextStyle().bigText(text))
        .setCategory(Notification.CATEGORY_CALL)
        .addAction(answer(context, "yes", id, question, 1))
        .addAction(answer(context, "no", id, question, 2))
        .build()
}

class ApproveReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val answer = intent.getStringExtra("answer") ?: return
        val id = intent.getStringExtra("id") ?: return
        val question = intent.getStringExtra("question") ?: "Cosmo needs your approval"
        val pending = goAsync()
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.cancel(APPROVAL_ID)
        Thread {
            val error = Bridge.post(context, "/approve", JSONObject().put("answer", answer).put("id", id), quick = true)
            val live = Bridge.live.value
            val settled = error != null && ("waiting" in error || (live.connected && live.approvalId != id))
            if (error != null && !settled) {
                CosmoService.shown = null
                nm.notify(APPROVAL_ID, approval(context, id, question, error))
            }
            pending.finish()
        }.start()
    }
}
