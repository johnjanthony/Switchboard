package io.github.johnjanthony.switchboard

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.johnjanthony.switchboard.network.Question
import io.github.johnjanthony.switchboard.ui.theme.SwitchboardTheme

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            SwitchboardTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    QuestionList(
                        questions = viewModel.questions.value,
                        onAnswer = { id, text -> viewModel.answerQuestion(id, text) }
                    )
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun QuestionList(
    questions: List<Question>,
    onAnswer: (String, String) -> Unit,
    modifier: Modifier = Modifier
) {
    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Switchboard") })
        }
    ) { padding ->
        LazyColumn(
            modifier = modifier.padding(padding),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            items(questions) { question ->
                QuestionCard(question, onAnswer)
            }
        }
    }
}

@Composable
fun QuestionCard(question: Question, onAnswer: (String, String) -> Unit) {
    var replyText by remember { mutableStateOf("") }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                text = "[${question.agent_id} | ${question.request_id}]",
                style = MaterialTheme.typography.labelMedium
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(text = question.question, style = MaterialTheme.typography.bodyLarge)
            
            if (question.suggestions != null) {
                Spacer(modifier = Modifier.height(16.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    question.suggestions.forEach { suggestion ->
                        Button(onClick = { onAnswer(question.request_id, suggestion) }) {
                            Text(suggestion)
                        }
                    }
                }
            } else {
                Spacer(modifier = Modifier.height(16.dp))
                OutlinedTextField(
                    value = replyText,
                    onValueChange = { replyText = it },
                    label = { Text("Your answer") },
                    modifier = Modifier.fillMaxWidth()
                )
                Spacer(modifier = Modifier.height(8.dp))
                Button(
                    onClick = { onAnswer(question.request_id, replyText) },
                    enabled = replyText.isNotBlank(),
                    modifier = Modifier.align(androidx.compose.ui.Alignment.End)
                ) {
                    Text("Send")
                }
            }
        }
    }
}
