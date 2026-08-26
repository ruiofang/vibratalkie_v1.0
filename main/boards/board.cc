#include "board.h"
#include "esp_log.h"

static Board *board = nullptr;

__attribute__((weak)) Board *create_board()
{
    ESP_LOGW("board", "board not created");
    return nullptr;
}

Board *get_board()
{
    if (board == nullptr)
    {
        board = create_board();
    }
    return board;
}
