package dev.notsg.cosmo

import android.Manifest
import android.content.Intent
import android.os.Bundle
import android.speech.RecognizerIntent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import org.json.JSONObject

private val Bg = Color(0xFF0E0F13)
private val Card = Color(0xFF181A20)
private val Muted = Color(0xFF8A8F9C)
private val Accent = Color(0xFF8AA4FF)
private val Danger = Color(0xFFFF6B6B)
private val Good = Color(0xFF5BD69A)

class MainActivity : ComponentActivity() {
    private var paired by mutableStateOf(false)
    private var message by mutableStateOf("")
    private var offered by mutableStateOf<Pairing?>(null)
    private var offeredLink = ""
    private val notifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
    private val scanner = registerForActivityResult(ScanContract()) { result -> result.contents?.let { pair(it) } }
    private val speech = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()?.let { send("/ask", JSONObject().put("text", it)) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        paired = Bridge.load(this) != null
        if (paired) startLink()
        intent?.dataString?.let { offer(it) }
        notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(background = Bg, surface = Card, primary = Accent)) {
                Surface(Modifier.fillMaxSize(), color = Bg) {
                    Box(Modifier.safeDrawingPadding().padding(16.dp)) {
                        if (paired) Home() else PairScreen()
                        offered?.let { Confirm(it) }
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.dataString?.let { offer(it) }
    }

    private fun offer(link: String) {
        offeredLink = link
        offered = Bridge.parse(link) ?: run { message = "That is not a Cosmo pairing link."; null }
    }

    @Composable
    private fun Confirm(pairing: Pairing) {
        AlertDialog(
            onDismissRequest = { offered = null },
            title = { Text(if (paired) "Replace this pairing?" else "Pair with this Mac?") },
            text = { Text("A link asked Cosmo to pair with ${pairing.hosts.joinToString(", ")} on port ${pairing.port}. Only continue if you opened it from your own Mac.") },
            confirmButton = { TextButton(onClick = { offered = null; pair(offeredLink) }) { Text("Pair") } },
            dismissButton = { TextButton(onClick = { offered = null }) { Text("Cancel") } },
        )
    }

    private fun startLink() = startForegroundService(Intent(this, CosmoService::class.java))

    private fun pair(link: String) {
        val pairing = Bridge.parse(link) ?: run { message = "That is not a Cosmo pairing link."; return }
        message = "Connecting…"
        Thread {
            val error = try {
                Bridge.call(pairing, "/state")
                null
            } catch (e: Exception) {
                e.message ?: "Could not reach the Mac"
            }
            runOnUiThread {
                if (error != null) {
                    message = error
                } else {
                    Bridge.save(this, link.trim())
                    message = ""
                    paired = true
                    startLink()
                }
            }
        }.start()
    }

    private fun unpair() {
        stopService(Intent(this, CosmoService::class.java))
        Bridge.save(this, null)
        paired = false
    }

    private fun send(path: String, body: JSONObject = JSONObject()) {
        Thread {
            val error = Bridge.post(this, path, body)
            runOnUiThread { message = error ?: "" }
        }.start()
    }

    @Composable
    private fun PairScreen() {
        var link by remember { mutableStateOf("") }
        Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center) {
            Text("Cosmo", fontSize = 34.sp, fontWeight = FontWeight.SemiBold, color = Color.White)
            Spacer(Modifier.height(8.dp))
            Text("On the Mac, run  python -m cosmo.phone  and scan the code it opens, or paste the link it prints.", color = Muted)
            Spacer(Modifier.height(24.dp))
            Button(onClick = {
                scanner.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE).setPrompt("Scan the Cosmo pairing code").setBeepEnabled(false).setOrientationLocked(false))
            }, Modifier.fillMaxWidth().height(52.dp)) { Text("Scan pairing code") }
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(link, { link = it }, Modifier.fillMaxWidth(), placeholder = { Text("cosmo://pair?…") }, singleLine = true)
            Spacer(Modifier.height(8.dp))
            OutlinedButton(onClick = { pair(link) }, Modifier.fillMaxWidth(), enabled = link.isNotBlank()) { Text("Pair with link") }
            if (message.isNotEmpty()) Text(message, color = Muted, modifier = Modifier.padding(top = 12.dp))
        }
    }

    @Composable
    private fun Home() {
        val live by Bridge.live.collectAsState()
        var draft by remember { mutableStateOf("") }
        val list = rememberLazyListState()
        LaunchedEffect(live.lines.size) { if (live.lines.isNotEmpty()) list.animateScrollToItem(live.lines.size - 1) }
        Column(Modifier.fillMaxSize().imePadding()) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                val color = when {
                    !live.connected -> Muted
                    live.state == "waiting_for_approval" -> Danger
                    live.state == "idle" -> Good
                    else -> Accent
                }
                Box(Modifier.size(10.dp).clip(CircleShape).background(color))
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text("Cosmo", fontSize = 22.sp, fontWeight = FontWeight.SemiBold, color = Color.White)
                    Text(if (live.connected) live.state.replace('_', ' ') else "connecting…", color = Muted, fontSize = 13.sp)
                }
                TextButton(onClick = { unpair() }) { Text("Unpair", color = Muted) }
                Button(onClick = { send("/stop") }, colors = ButtonDefaults.buttonColors(containerColor = Danger)) { Text("Stop", color = Color.White) }
            }
            live.approval?.let { question ->
                Column(Modifier.padding(top = 14.dp).fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(Card).padding(14.dp)) {
                    Text("Cosmo wants to", color = Muted, fontSize = 13.sp)
                    Text(question, color = Color.White, modifier = Modifier.padding(vertical = 8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Button(onClick = { send("/approve", JSONObject().put("answer", "yes").put("id", live.approvalId)) }, Modifier.weight(1f), enabled = live.approvalId != null) { Text("Allow") }
                        OutlinedButton(onClick = { send("/approve", JSONObject().put("answer", "no").put("id", live.approvalId)) }, Modifier.weight(1f), enabled = live.approvalId != null) { Text("Deny") }
                    }
                }
            }
            if (live.tasks.isNotEmpty()) {
                Column(Modifier.padding(top = 14.dp)) {
                    Text("Running", color = Muted, fontSize = 13.sp)
                    live.tasks.forEach { Text("• ${it.title}", color = Color.White, fontSize = 14.sp, modifier = Modifier.padding(top = 4.dp)) }
                }
            }
            LazyColumn(Modifier.weight(1f).padding(vertical = 14.dp), state = list, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(live.lines) { line ->
                    val mine = line.who == "you"
                    Box(Modifier.fillMaxWidth(), contentAlignment = if (mine) Alignment.CenterEnd else Alignment.CenterStart) {
                        Text(
                            line.text,
                            color = Color.White,
                            modifier = Modifier.fillMaxWidth(0.85f).wrapContentWidth(if (mine) Alignment.End else Alignment.Start)
                                .clip(RoundedCornerShape(14.dp)).background(if (mine) Color(0xFF2A3350) else Card).padding(12.dp),
                        )
                    }
                }
            }
            if (message.isNotEmpty()) Text(message, color = Danger, fontSize = 13.sp, modifier = Modifier.padding(bottom = 6.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(draft, { draft = it }, Modifier.weight(1f), placeholder = { Text("Ask Cosmo") }, maxLines = 4)
                Spacer(Modifier.width(8.dp))
                FilledTonalButton(onClick = {
                    try {
                        speech.launch(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM))
                    } catch (_: Exception) {
                        message = "No speech recognizer on this phone."
                    }
                }, contentPadding = PaddingValues(0.dp), modifier = Modifier.size(52.dp)) { Text("🎙") }
                Spacer(Modifier.width(8.dp))
                Button(onClick = {
                    send("/ask", JSONObject().put("text", draft.trim()))
                    draft = ""
                }, enabled = draft.isNotBlank(), modifier = Modifier.height(52.dp)) { Text("Send") }
            }
        }
    }
}
