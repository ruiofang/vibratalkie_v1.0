#ifndef MEMO_H
#define MEMO_H

#include <string>
#include <vector>
#include <ctime>
#include <cJSON.h>

// Represents a single memo entry
struct Memo {
    std::string id;           // Unique identifier
    std::string title;        // Memo title
    std::string content;      // Memo content
    int64_t created_at;       // Creation timestamp
    int64_t updated_at;       // Last update timestamp

    // Convert to JSON object
    cJSON* ToJson() const;

    // Create from JSON object
    static Memo FromJson(const cJSON* json);
};

// Manages memo storage (max 3 memos)
class MemoManager {
public:
    MemoManager();
    ~MemoManager();

    // Add a new memo (returns the ID of the added memo)
    std::string AddMemo(const std::string& title, const std::string& content);

    // Get a memo by ID
    bool GetMemo(const std::string& id, Memo& memo) const;

    // List all memos
    std::vector<Memo> ListMemos() const;

    // Update a memo
    bool UpdateMemo(const std::string& id, const std::string& title, const std::string& content);

    // Delete a memo
    bool DeleteMemo(const std::string& id);

    // Delete all memos
    void DeleteAll();

    // Get count of memos
    int GetCount() const;

    // Max number of memos
    static constexpr int MAX_MEMOS = 3;

private:
    std::vector<Memo> memos_;

    // Load memos from NVS
    void Load();

    // Save memos to NVS
    void Save();

    // Generate a unique ID
    std::string GenerateId();
};

#endif // MEMO_H
