#include "memo.h"
#include "settings.h"
#include <esp_log.h>
#include <sstream>
#include <iomanip>
#include <algorithm>

#define TAG "Memo"
#define MEMO_NAMESPACE "memo"
#define MEMO_STORAGE_KEY "memos"

// Convert Memo to JSON
cJSON* Memo::ToJson() const {
    cJSON* json = cJSON_CreateObject();
    cJSON_AddStringToObject(json, "id", id.c_str());
    cJSON_AddStringToObject(json, "title", title.c_str());
    cJSON_AddStringToObject(json, "content", content.c_str());
    cJSON_AddNumberToObject(json, "created_at", (double)created_at);
    cJSON_AddNumberToObject(json, "updated_at", (double)updated_at);
    return json;
}

// Create Memo from JSON
Memo Memo::FromJson(const cJSON* json) {
    Memo memo;
    if (json == nullptr) {
        return memo;
    }

    const cJSON* id_item = cJSON_GetObjectItem(json, "id");
    const cJSON* title_item = cJSON_GetObjectItem(json, "title");
    const cJSON* content_item = cJSON_GetObjectItem(json, "content");
    const cJSON* created_at_item = cJSON_GetObjectItem(json, "created_at");
    const cJSON* updated_at_item = cJSON_GetObjectItem(json, "updated_at");

    if (id_item && id_item->valuestring) {
        memo.id = id_item->valuestring;
    }
    if (title_item && title_item->valuestring) {
        memo.title = title_item->valuestring;
    }
    if (content_item && content_item->valuestring) {
        memo.content = content_item->valuestring;
    }
    if (created_at_item) {
        memo.created_at = (int64_t)created_at_item->valuedouble;
    }
    if (updated_at_item) {
        memo.updated_at = (int64_t)updated_at_item->valuedouble;
    }

    return memo;
}

MemoManager::MemoManager() {
    Load();
}

MemoManager::~MemoManager() {
}

std::string MemoManager::GenerateId() {
    // Generate a simple ID based on timestamp
    static unsigned int counter = 0;
    int64_t now = std::time(nullptr);
    std::ostringstream oss;
    oss << std::hex << now << "_" << (++counter);
    return oss.str();
}

std::string MemoManager::AddMemo(const std::string& title, const std::string& content) {
    if (memos_.size() >= MAX_MEMOS) {
        ESP_LOGW(TAG, "Cannot add memo: maximum limit (%d) reached", MAX_MEMOS);
        return "";
    }

    Memo memo;
    memo.id = GenerateId();
    memo.title = title;
    memo.content = content;
    int64_t now = std::time(nullptr);
    memo.created_at = now;
    memo.updated_at = now;

    memos_.push_back(memo);
    Save();

    ESP_LOGI(TAG, "Added memo with ID: %s", memo.id.c_str());
    return memo.id;
}

bool MemoManager::GetMemo(const std::string& id, Memo& memo) const {
    auto it = std::find_if(memos_.begin(), memos_.end(),
        [&id](const Memo& m) { return m.id == id; });

    if (it != memos_.end()) {
        memo = *it;
        return true;
    }
    return false;
}

std::vector<Memo> MemoManager::ListMemos() const {
    return memos_;
}

bool MemoManager::UpdateMemo(const std::string& id, const std::string& title, const std::string& content) {
    auto it = std::find_if(memos_.begin(), memos_.end(),
        [&id](const Memo& m) { return m.id == id; });

    if (it != memos_.end()) {
        it->title = title;
        it->content = content;
        it->updated_at = std::time(nullptr);
        Save();
        ESP_LOGI(TAG, "Updated memo with ID: %s", id.c_str());
        return true;
    }
    return false;
}

bool MemoManager::DeleteMemo(const std::string& id) {
    auto it = std::find_if(memos_.begin(), memos_.end(),
        [&id](const Memo& m) { return m.id == id; });

    if (it != memos_.end()) {
        memos_.erase(it);
        Save();
        ESP_LOGI(TAG, "Deleted memo with ID: %s", id.c_str());
        return true;
    }
    return false;
}

void MemoManager::DeleteAll() {
    memos_.clear();
    Save();
    ESP_LOGI(TAG, "Deleted all memos");
}

int MemoManager::GetCount() const {
    return static_cast<int>(memos_.size());
}

void MemoManager::Load() {
    Settings settings(MEMO_NAMESPACE, false);  // Read-only
    std::string data = settings.GetString(MEMO_STORAGE_KEY, "");

    if (data.empty()) {
        ESP_LOGI(TAG, "No memos found in storage");
        return;
    }

    cJSON* root = cJSON_Parse(data.c_str());
    if (root == nullptr) {
        ESP_LOGE(TAG, "Failed to parse memos JSON");
        return;
    }

    if (!cJSON_IsArray(root)) {
        ESP_LOGE(TAG, "Memos data is not an array");
        cJSON_Delete(root);
        return;
    }

    cJSON* item = nullptr;
    cJSON_ArrayForEach(item, root) {
        Memo memo = Memo::FromJson(item);
        if (!memo.id.empty()) {
            memos_.push_back(memo);
        }
    }

    cJSON_Delete(root);
    ESP_LOGI(TAG, "Loaded %zu memos from storage", memos_.size());
}

void MemoManager::Save() {
    // Create JSON array
    cJSON* root = cJSON_CreateArray();

    for (const auto& memo : memos_) {
        cJSON* memo_json = memo.ToJson();
        cJSON_AddItemToArray(root, memo_json);
    }

    // Convert to string
    char* json_str = cJSON_PrintUnformatted(root);
    std::string data(json_str);
    cJSON_free(json_str);

    // Save to NVS
    Settings settings(MEMO_NAMESPACE, true);  // Read-write
    settings.SetString(MEMO_STORAGE_KEY, data);

    cJSON_Delete(root);
    ESP_LOGI(TAG, "Saved %zu memos to storage", memos_.size());
}
