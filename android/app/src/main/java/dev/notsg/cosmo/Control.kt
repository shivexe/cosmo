package dev.notsg.cosmo

import android.Manifest
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Base64
import org.json.JSONObject
import java.net.Inet4Address
import java.net.NetworkInterface

const val USING_ID = 3

object Wireless {
    @Volatile var endpoint = ""
        private set
    private var listener: NsdManager.DiscoveryListener? = null
    private val callbacks = mutableMapOf<String, NsdManager.ServiceInfoCallback>()
    private val main = Handler(Looper.getMainLooper())
    private val settings = listOf("development_settings_enabled", "adb_enabled", "adb_wifi_enabled")
    private const val IDLE = 120000L
    @Volatile var lastUse = 0L

    fun allowed(context: Context) = context.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS) == PackageManager.PERMISSION_GRANTED

    fun on(context: Context) = Settings.Global.getInt(context.contentResolver, "adb_wifi_enabled", 0) == 1

    private fun prefs(context: Context) = context.getSharedPreferences("wireless", Context.MODE_PRIVATE)

    // dev mode is on only while cosmo uses the phone; bank apps refuse to open while it is on
    @Synchronized fun turnOn(context: Context): Boolean {
        lastUse = System.currentTimeMillis()
        prefs(context).edit().putLong("lastUse", lastUse).apply()
        if (!allowed(context)) return false
        val connectivity = context.getSystemService(ConnectivityManager::class.java)
        if (connectivity.getNetworkCapabilities(connectivity.activeNetwork)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) != true) return false
        val resolver = context.contentResolver
        return try {
            val changed = prefs(context).getStringSet("changed", emptySet())!!.toMutableSet()
            for (setting in settings) {
                if (Settings.Global.getInt(resolver, setting, 0) != 1) {
                    Settings.Global.putInt(resolver, setting, 1)
                    changed += setting
                }
            }
            prefs(context).edit().putStringSet("changed", changed).apply()
            true
        } catch (_: Exception) {
            false
        }
    }

    fun used(context: Context) {
        lastUse = System.currentTimeMillis()
        prefs(context).edit().putLong("lastUse", lastUse).apply()
    }

    // turns back off only what cosmo itself turned on, after two idle minutes
    @Synchronized fun idleOff(context: Context, now: Boolean = false) {
        val changed = prefs(context).getStringSet("changed", emptySet())!!
        val used = maxOf(lastUse, prefs(context).getLong("lastUse", 0L))
        if (changed.isEmpty() || (!now && System.currentTimeMillis() - used < IDLE) || !allowed(context)) return
        try {
            for (setting in settings.reversed()) if (setting in changed) Settings.Global.putInt(context.contentResolver, setting, 0)
        } catch (_: Exception) {
        }
        prefs(context).edit().remove("changed").apply()
        endpoint = ""
    }

    @Suppress("DEPRECATION")
    fun watch(context: Context) = main.post {
        if (listener != null) return@post
        val manager = context.getSystemService(NsdManager::class.java)
        val found = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(type: String) {}
            override fun onDiscoveryStopped(type: String) {}
            override fun onStartDiscoveryFailed(type: String, code: Int) { main.post { listener = null } }
            override fun onStopDiscoveryFailed(type: String, code: Int) {}
            override fun onServiceLost(info: NsdServiceInfo) {
                if (Build.VERSION.SDK_INT >= 34) main.post {
                    callbacks.remove(info.serviceName)?.let { try { manager.unregisterServiceInfoCallback(it) } catch (_: Exception) {} }
                }
            }
            override fun onServiceFound(info: NsdServiceInfo) {
                if (Build.VERSION.SDK_INT >= 34) {
                    main.post {
                        val name = info.serviceName
                        if (name in callbacks || callbacks.size >= 16) return@post
                        val cb = object : NsdManager.ServiceInfoCallback {
                            override fun onServiceUpdated(updated: NsdServiceInfo) = accept(updated)
                            override fun onServiceLost() {}
                            override fun onServiceInfoCallbackRegistrationFailed(code: Int) { main.post { callbacks.remove(name) } }
                            override fun onServiceInfoCallbackUnregistered() {}
                        }
                        callbacks[name] = cb
                        try { manager.registerServiceInfoCallback(info, { main.post(it) }, cb) } catch (_: Exception) { callbacks.remove(name) }
                    }
                } else {
                    manager.resolveService(info, object : NsdManager.ResolveListener {
                        override fun onResolveFailed(info: NsdServiceInfo, code: Int) {}
                        override fun onServiceResolved(info: NsdServiceInfo) = accept(info)
                    })
                }
            }
        }
        listener = found
        try { manager.discoverServices("_adb-tls-connect._tcp.", NsdManager.PROTOCOL_DNS_SD, found) } catch (_: Exception) { listener = null }
    }

    fun stop(context: Context) = main.post {
        val manager = context.getSystemService(NsdManager::class.java)
        if (Build.VERSION.SDK_INT >= 34) callbacks.values.forEach { try { manager.unregisterServiceInfoCallback(it) } catch (_: Exception) {} }
        callbacks.clear()
        listener?.let { try { manager.stopServiceDiscovery(it) } catch (_: Exception) {} }
        listener = null
        endpoint = ""
    }

    @Suppress("DEPRECATION")
    private fun accept(info: NsdServiceInfo) {
        val own = try { NetworkInterface.getNetworkInterfaces().toList().flatMap { it.inetAddresses.toList() } } catch (_: Exception) { emptyList() }
        val addresses = if (Build.VERSION.SDK_INT >= 34) info.hostAddresses else listOfNotNull(info.host)
        val ip = addresses.firstOrNull { it is Inet4Address && it in own }?.hostAddress ?: return
        if (info.port in 1..65535) endpoint = "$ip:${info.port}"
    }
}

class TypeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val text = intent.getStringExtra("b64")?.let { runCatching { String(Base64.decode(it, Base64.DEFAULT)) }.getOrNull() } ?: return
        context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Cosmo", text))
        resultCode = 1
    }
}

class StopReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val pending = goAsync()
        context.getSystemService(NotificationManager::class.java).cancel(USING_ID)
        Thread {
            Bridge.post(context, "/stop", quick = true)
            pending.finish()
        }.start()
    }
}

fun usingPhone(context: Context, event: JSONObject) {
    val detail = event.optJSONObject("detail") ?: return
    if (detail.optString("device") != "phone") return
    val app = detail.optString("app")
    val stop = PendingIntent.getBroadcast(context, 3, Intent(context, StopReceiver::class.java), PendingIntent.FLAG_IMMUTABLE)
    val note = Notification.Builder(context, "using")
        .setSmallIcon(R.drawable.ic_cosmo)
        .setContentTitle("Cosmo is using your phone")
        .setContentText(listOf(app.removePrefix("Phone").removePrefix(" · "), detail.optString("action")).filter { it.isNotBlank() }.joinToString(" · "))
        .setOngoing(true)
        .setOnlyAlertOnce(true)
        .setTimeoutAfter(15000)
        .addAction(Notification.Action.Builder(null, "Stop", stop).build())
        .build()
    context.getSystemService(NotificationManager::class.java).notify(USING_ID, note)
}
