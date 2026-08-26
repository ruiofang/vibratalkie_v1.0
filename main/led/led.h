#ifndef _LED_H_
#define _LED_H_

class Led {
public:
    virtual ~Led() = default;
    // Set the led state based on the device state
    virtual void OnStateChanged() = 0;

    // Optional hook for boards that support LED sleep mode indication
    virtual void SetSleepMode(bool /*enabled*/) {}
};


class NoLed : public Led {
public:
    virtual void OnStateChanged() override {}
    
    virtual void SetSleepMode(bool /*enabled*/) override {}
};

#endif // _LED_H_
